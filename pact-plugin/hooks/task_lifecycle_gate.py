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
  - variety_missing_on_dispatch_task — pact-* work Task created with
    metadata.variety present but malformed per-dimension rationales OR no
    resolvable variety total. (The ABSENT-stamp arm was moved to the
    dispatch-boundary gate in handoff_ordering_gate.py per the #865 surgical
    split — this PostToolUse rule keeps only the present-but-malformed checks.)
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
# This cap bounds MEMORY only — it does NOT reject sub-cap input: a frame
# with a valid JSON prefix still parses (harmless — degraded never grants
# allow, primary fails-open).
# VALUE MUST EQUAL bootstrap_gate._STDIN_READ_MAX (twin-VALUE discipline).
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


def _bounded_error_text(error: BaseException) -> str:
    """Sanitized, length-bounded rendering of an exception for embedding in
    context-bound warning text: control/non-printable characters become
    spaces, and the result is truncated to _ERROR_TEXT_MAX chars with an
    explicit marker. Full text still goes to stderr at the call site.

    Total over hostile exceptions, structurally: the type name is captured
    first — a metaclass can make __name__ a property that raises (caught;
    falls back to a literal) or return any non-str value, INCLUDING a str
    subclass whose own __str__/__format__ raises. The exact-type check below
    (type(...) is str, which rejects str subclasses too) reduces type_name to
    an EXACT str, whose __format__/__str__ are str's own built-ins and cannot
    be overridden — so neither f-string branch below can raise on type_name
    regardless of the original __name__ value. The only exception-owned code
    left is the message render (error's own __str__), isolated to the main
    branch and guarded by the fallback. The function therefore returns a
    string for ANY exception object."""
    try:
        type_name = type(error).__name__
    except BaseException:  # noqa: BLE001 — hostile metaclass __name__ must not escape
        type_name = "exception"
    # __name__ can also RETURN (not raise) a non-str value — including a str
    # SUBCLASS whose own __str__/__format__ raises, which an isinstance check
    # would wave through. An EXACT-type check (type(...) is str) rejects
    # subclasses too, so type_name is provably an exact str whose formatting
    # uses str's own unpatchable built-ins → both f-string branches below
    # (incl. the fallback, which re-interpolates type_name) cannot raise on it.
    if type(type_name) is not str:
        type_name = "exception"
    try:
        text = f"{type_name}: {error}"
    except BaseException:  # noqa: BLE001 — hostile __str__ must not escape the renderer
        text = f"{type_name}: <exception str() raised>"
    truncated = len(text) > _ERROR_TEXT_MAX
    if truncated:
        # MemoryError-safe by STRUCTURE: bounding first keeps the sanitize
        # join O(cap) not O(n) — a multi-GB input never materializes a
        # sanitized copy; asserted structurally, not via a runtime test.
        text = text[:_ERROR_TEXT_MAX]        # bound BEFORE the O(n) sanitize join
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    if truncated:
        text = text + "...[truncated]"
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
            try:
                print(
                    "task_lifecycle_gate: gate_health journal emit skipped "
                    "(append_event returned False)",
                    file=sys.stderr,
                )
            except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
                pass
    except BaseException:  # noqa: BLE001 — the crash handler must not crash:
        # mirror the import gauntlet's breadth (except BaseException at the
        # wrapped-import block). The lazy imports execute arbitrary module
        # bodies — a module-level sys.exit or KeyboardInterrupt surfacing
        # here would exit nonzero AFTER the floor marker printed, and stdout
        # JSON is only honored on exit 0.
        try:
            print(
                "task_lifecycle_gate: gate_health journal emit unavailable "
                "(late import or init failed)",
                file=sys.stderr,
            )
        except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
            pass


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
    # the FLOOR below must print no matter what the renderer does. The
    # fallback is a raise-proof CONSTANT — type(error).__name__ would
    # re-invoke the same attribute access (hostile metaclass __name__) that
    # is the helper's one remaining fall-through path.
    try:
        error_text = _bounded_error_text(error)
    except BaseException:  # noqa: BLE001 — floor must survive any renderer defect
        error_text = "<error text unavailable>"
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
    try:
        print(
            f"Hook load error (task_lifecycle_gate / {stage}): {error_full}",  # full text
            file=sys.stderr,
        )
    except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
        pass
    _emit_gate_health_event(stage, error_text, input_data)      # bonus LAST
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ─────────────────────────
try:
    import re

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
    from shared.session_journal import (
        append_event,
        get_journal_path,
        make_event,
        read_events,
    )
    from shared.task_metadata_snapshot import (
        PER_WRITE_MIRROR_KEYS,
        emit_task_metadata_snapshot,
    )
    from shared.task_utils import is_teachback_subject as _is_teachback_subject
    from shared.task_utils import read_task_json
    from shared.teachback_schema import (
        DISPATCH_VARIETY_KEYS,
        TEACHBACK_OBJECT_FIELDS,
        TEACHBACK_RECOMMENDED_BAND_MIN,
        TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
        TEACHBACK_REQUIRED_FIELDS,
        TEACHBACK_REQUIRED_SUBKEYS,
        TEACHBACK_SCHEMA_ECHO,
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
# rules below. TEACHBACK_SCHEMA_ECHO (also from the SSOT) is appended to the
# teachback_submit_schema_invalid advisory so the deny message echoes the full
# canonical schema, not only the offending field(s).

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


# ─── artifact_paths emit backstop (durability nudge) ─────────────────────────
#
# Maps a phase-task subject PREFIX to its artifact_paths `workflow` tag, but
# ONLY for the phases that MUST emit a durable artifact pointer — PREPARE and
# ARCHITECT, which write recoverable disk artifacts (docs/preparation/,
# docs/architecture/) that the git-immune, GC-durable journal event protects.
# CODE:/TEST:/ATOMIZE:/CONSOLIDATE: phases are DELIBERATELY ABSENT: their output
# is git-tracked (CODE/TEST) or non-artifact (ATOMIZE/CONSOLIDATE), so a missing
# artifact_paths event is expected, not an anomaly — leaving them out makes the
# backstop silently skip them. The `workflow` values mirror the lowercase enum
# in session_journal's artifact_paths registration (plan-mode / prepare /
# architect / peer-review / code-auditor); only the two phase-task-backed
# workflows appear here (plan-mode / peer-review are command syntheses with no
# phase-completion TaskUpdate for this PostToolUse hook to observe).
_ARTIFACT_EMIT_PHASE_WORKFLOWS = {
    "PREPARE:": "prepare",
    "ARCHITECT:": "architect",
}


def _phase_artifact_requirement(subject: str) -> "tuple[str, str] | None":
    """Map a phase-task subject to its (workflow, feature) artifact key, or
    None when this subject does not require an artifact_paths emit.

    Returns None (skip the backstop nudge) for:
      - a non-PREPARE/ARCHITECT subject (CODE/TEST/ATOMIZE/CONSOLIDATE/other →
        exempt by design — their artifacts are git-tracked or absent), and
      - a malformed phase subject with no ": " feature separator (rare; the
        orchestrate command creates phase tasks as exactly "PREPARE: {feature}"
        / "ARCHITECT: {feature}", so a missing separator is degenerate — fail
        open as a no-op rather than nudging on an unresolvable feature slug).

    The feature slug is the subject suffix after the "{PHASE}: " prefix; it must
    equal the orchestrate {feature-slug} that the lead-frame emit site writes as
    the event's `feature` field, so the (workflow, feature) presence check
    aligns with the dedup SSOT key. Pure; never raises.
    """
    if not isinstance(subject, str):
        return None
    for prefix, workflow in _ARTIFACT_EMIT_PHASE_WORKFLOWS.items():
        if subject.startswith(prefix):
            # Split on the canonical "{PHASE}: " separator; the suffix is the
            # feature slug. A subject that startswith the bare prefix but has no
            # ": " (e.g. "PREPAREthing") cannot match prefix anyway; one that is
            # exactly the prefix with nothing after yields an empty slug → skip.
            _, sep, suffix = subject.partition(": ")
            feature = suffix.strip()
            if not sep or not feature:
                return None
            return workflow, feature
    return None


def _artifact_paths_event_present(workflow: str, feature: str) -> bool:
    """Return True iff an artifact_paths journal event exists for this
    (workflow, feature) in the CURRENT session's journal.

    Uses the IMPLICIT read_events() (lead-frame): this backstop is is_lead-gated
    at its single call site, so get_session_dir() resolves the canonical journal
    in the lead's process. The off-lead masked-read hazard (read_events()
    false-returning [] for a teammate frame) is strictly the SECRETARY HARVEST
    reader's concern — that reader runs off-lead and MUST use
    read_events_from(session_dir); this backstop does not, because it never runs
    off-lead. The hook frame carries no worktree/session_dir to pass anyway.

    Fail-OPEN by construction: read_events swallows its own errors and returns []
    (treated as "absent" → the nudge fires). A false nudge is non-blocking and
    self-corrects; a false-silence (missing the forgotten emit) is the dangerous
    error this presence check exists to prevent, so absent-on-error is safe-side.
    Pure read; never raises (read_events is internally fail-open).
    """
    events = read_events("artifact_paths")
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("workflow") == workflow and event.get("feature") == feature:
            return True
    return False


# `_is_teachback_subject` (the canonical Teachback Task subject predicate) was
# HOISTED to shared/task_utils.py so the handoff_ordering_gate PreToolUse hook
# can reuse it without importing this PostToolUse module. It is re-imported at
# the top of this file as `_is_teachback_subject` (private-name alias preserving
# the gate's existing call sites). SINGLE definition lives in task_utils; do NOT
# re-introduce the regex here — duplication reopens the drift class.


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
        # #1165 parity: sanitize task_id at intake, mirroring b1's intake
        # pattern (agent_handoff_emitter sanitizes before its filesystem
        # sinks). Covers BOTH b2 call sites (acceptance-commit + the
        # write-after-completion backstop route through this function).
        # Without this, a pathological task_id yields a b1/b2 divergence:
        # the emitted event's task_id form differs from b1's (breaking the
        # reader's cross-family join) and the O_EXCL marker key can split
        # (b1 claims the sanitized form, b2 the raw form → dedup miss →
        # double emit). The marker resolver's own internal sanitization is
        # retained — this intake pass is what aligns the EVENT field.
        task_id = sanitize_path_component(str(task_id))
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


def _emit_per_write_snapshot(
    team_name: str,
    task_id: str,
    delta: dict,
    task: dict | None = None,
) -> None:
    """Per-write mirror emit — hermetic; never raises; READ-ONLY on every
    input (new dicts only: the overlay comprehension builds a fresh
    mapping; neither ``delta``, ``task``, nor ``task['metadata']`` is
    written). ``task=None`` → one read_task_json; the TaskUpdate leg passes
    its already-read task_a (zero marginal disk cost).

    Shared by the TaskCreate and TaskUpdate per-write legs. Degenerate
    inputs are the substrate's job (sentinel subject, owner normalize,
    empty-payload no-emit); ``task_id`` sanitization also happens inside
    ``emit_task_metadata_snapshot()``'s content-key-claim path — no new
    sanitizer here. A missing/drained task file (``task == {}``) degrades
    to a delta-only payload — correct: mirror what the write shows us. A
    None-valued delta key is the platform DELETE op; the overlay drops it
    so the emit mirrors the post-delete disk state (deduping if unchanged).
    """
    try:
        if task is None:
            task = read_task_json(task_id, team_name) if team_name else {}
        if not isinstance(task, dict):
            task = {}
        disk_md = (
            task.get("metadata")
            if isinstance(task.get("metadata"), dict)
            else {}
        )
        emit_task_metadata_snapshot(
            team_name,
            task_id,
            task.get("subject") or "",
            task.get("owner"),
            {**disk_md, **{k: v for k, v in delta.items() if v is not None}},
        )
    except Exception:
        # Exit-0 advisory contract: a mirror failure must never block or
        # annotate the hooked tool call.
        pass


def _created_task_id(tool_response: object, tool_input: dict) -> str:
    """Id of the task a TaskCreate just made: tool_response.task.id
    (canonical — the id does not exist in tool_input), falling back to a
    harness-echoed tool_input.taskId. "" when unresolvable.

    Extracted from the dispatch_variety emit block (behavior-identical) so
    the per-write TaskCreate leg and the dispatch_variety emit resolve the
    id through ONE expression of the create-result post-state shape.
    """
    created_task = (
        tool_response.get("task") if isinstance(tool_response, dict) else None
    )
    new_task_id = ""
    if isinstance(created_task, dict):
        new_task_id = str(created_task.get("id") or "")
    if not new_task_id:
        new_task_id = str(tool_input.get("taskId") or "")
    return new_task_id


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
    # Non-empty-string check on the string fields; the object fields
    # (variety_acknowledgment) are validated by the dedicated sub-validator
    # below. The carve-out derives from TEACHBACK_OBJECT_FIELDS (SSOT) so this
    # string/object partition stays in lockstep with the schema-echo derivation.
    string_fields = tuple(
        f for f in TEACHBACK_REQUIRED_FIELDS if f not in TEACHBACK_OBJECT_FIELDS
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


def _band_from_total(total: int) -> str:
    """Map a resolved variety total to a reasoning_reconstruction band
    string ("required" / "recommended" / "skipped"). Shared by the direct
    Task-B resolution path and the C2 parent-inheritance fallback so both
    apply the identical threshold logic (one band-cut SSOT)."""
    if total >= TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN:
        return "required"
    if total >= TEACHBACK_RECOMMENDED_BAND_MIN:
        return "recommended"
    return "skipped"


def _inherit_band_from_parent(task_b: dict, team_name: str) -> str | None:
    """#891 Opt2 parent-inheritance fallback: when Task B carries no
    resolvable variety, inherit the band from its PARENT task's variety
    total. Returns a band string when the parent resolves, else None
    (caller treats None as "unresolvable" — the preserved floor).

    Modular/separable: this is the entire Opt2 surface. Deleting this
    function + its two call sites reverts to Opt1-alone behavior.

    Parent resolution + guardrail (don't inherit a WRONG parent's band —
    a wrong inherit mis-resolves the band, the exact bug being fixed):
      - Task B blocks the parent (Plan/feature/umbrella) task. The parent
        pointer is task_b.blocks[0], but only when blocks is UNAMBIGUOUS:
        a singleton list. Empty, multi-entry, or non-list blocks → fail
        open (None) rather than guess among candidates.
      - The parent must itself carry a resolvable variety total (via the
        shared resolve_variety_total, reused unchanged). A non-parent task
        (phase / teachback gate) does not carry variety, so a mis-pointed
        blocks[0] fails open here rather than inheriting a wrong band. This
        is the structural "looks like a Plan/feature task" guardrail: the
        defining property of an inheritable parent is that it is stamped.
    """
    blocks = task_b.get("blocks")
    # Singleton-only: >1 entry is ambiguous (which is the parent?); 0/None
    # has no parent pointer. Either way fail open.
    if not isinstance(blocks, list) or len(blocks) != 1:
        return None
    parent_id = blocks[0]
    if not isinstance(parent_id, str) or not parent_id:
        return None
    parent = read_task_json(parent_id, team_name)
    if not parent:
        return None
    parent_metadata = parent.get("metadata")
    if not isinstance(parent_metadata, dict):
        return None
    parent_variety = parent_metadata.get("variety")
    if not isinstance(parent_variety, dict):
        return None
    parent_total = resolve_variety_total(parent_variety, parent_metadata)
    if parent_total is None:
        return None
    return _band_from_total(parent_total)


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
        variety absent/malformed/untotaled on Task B AND the parent
        inheritance fallback also failed (fail-open: caller emits a separate
        band_unresolvable advisory documenting the gap)

    The total is resolved via the shared resolve_variety_total helper, so a
    non-canonical stamp (score / top-level variety_score / dimension-sum)
    resolves rather than reading as "unresolvable" — the same resolver the
    write-time validator consults (the cross-rule consistency property).

    #891 Opt2: when Task B has no resolvable variety, the band is inherited
    from the parent (Plan/feature/umbrella) task before returning
    "unresolvable" — see _inherit_band_from_parent. This keeps an unstamped
    Task B's band resolvable (consultations are frequently 11-13) instead of
    silently mis-resolving as "skipped".
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
    # Opt2 broadened the inherit trigger: a metadata-not-dict OR variety-not-dict
    # Task B (not only an absent variety) now resolves total=None and flows to
    # the parent-inherit fallback below, rather than returning "unresolvable"
    # immediately as the pre-Opt2 code did. The floor is preserved (an
    # unresolvable parent still yields "unresolvable").
    variety = metadata.get("variety") if isinstance(metadata, dict) else None
    total = (
        resolve_variety_total(variety, metadata)
        if isinstance(variety, dict)
        else None
    )
    if total is None:
        # Task B variety absent/malformed/untotaled → try parent inheritance
        # (Opt2) before conceding "unresolvable". Floor preserved: a parent
        # that also fails to resolve returns None → "unresolvable".
        return _inherit_band_from_parent(task_b, team_name) or "unresolvable"
    return _band_from_total(total)


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
        # SURGICAL SPLIT (#865 enforce-vs-advise re-scope): the ABSENT-stamp
        # arm (variety entirely missing on a dispatched Task B) was MOVED to
        # the dispatch-boundary gate in handoff_ordering_gate.py — that is the
        # FIRST-OBSERVABLE-WRITE concern with a timing constraint (catch it at
        # the terminal owner+addBlockedBy wiring write, deterministically). R4
        # KEEPS the present-but-malformed arm: a variety that IS stamped but
        # carries malformed per-dimension rationales OR no resolvable total.
        # These are post-write QUALITY checks with no dispatch-boundary timing
        # constraint, so they stay a PostToolUse advisory here. The rule name
        # is retained for the malformed-present paths; the bare-absent path no
        # longer fires here (it fires at the wiring write). Clause order
        # mirrors L575-580: cheap dict-lookups first, disk-touching
        # exempt-check last (cached via `_exempt()`).
        if (
            not is_teachback
            and owner.startswith("pact-")
            and not _exempt()
        ):
            incoming_metadata = tool_input.get("metadata") or {}
            incoming_variety = incoming_metadata.get("variety")
            # ABSENT variety is no longer enforced here (moved to the wiring
            # write). Only validate a variety that IS present.
            if incoming_variety:
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

        # #955 dispatch_variety emit — GC-immune mirror of the per-dispatch
        # variety stamp. Fires on the TaskCreate of a Task-B carrying
        # metadata.variety (one per dispatch, D3). Keyed on is_lead +
        # metadata.variety PRESENCE — NOT on owner (per orchestrate.md the
        # TaskCreate(B) sets metadata.variety but leaves owner empty; owner is
        # wired by a SEPARATE later TaskUpdate, so an owner gate here would never
        # fire). The new Task-B id comes from the create-result post-state
        # (tool_response.task.id — the same shape the ③ completion branch reads),
        # falling back to tool_input.taskId if the harness echoes it. Best-effort:
        # a missing id or an append failure skips the emit (coverage degrades by
        # one dispatch) but never breaks the gate's advisory evaluation or its
        # exit-0 contract.
        if pact_context.is_lead(input_data):
            create_variety = (
                incoming_metadata.get("variety")
                if isinstance(incoming_metadata, dict)
                else None
            )
            if isinstance(create_variety, dict) and create_variety:
                new_task_id = _created_task_id(tool_response, tool_input)
                if new_task_id:
                    # §5.1-fidelity projection: mirror ONLY the 4 dimensions +
                    # total, dropping the *_rationale strings — the journal is
                    # the GC-immune CALIBRATION source (wrap-up Q5 reads only
                    # .total), not a rationale archive. Keys come from the
                    # canonical DISPATCH_VARIETY_KEYS (derived from
                    # _VARIETY_DIMENSIONS) so a dimension rename never drifts.
                    # `if k in` keeps it tolerant of a partial stamp.
                    projected_variety = {
                        k: create_variety[k]
                        for k in DISPATCH_VARIETY_KEYS
                        if k in create_variety
                    }
                    # Append-only, no dedup by design: a Task-B is created ONCE,
                    # so this fires naturally-once per dispatch (unlike
                    # agent_handoff, which can re-fire across b1/b2/backstop and
                    # therefore needs the O_EXCL occupant marker).
                    try:
                        append_event(make_event(
                            "dispatch_variety",
                            task_id=new_task_id,
                            variety=projected_variety,
                        ))
                    except Exception:
                        pass  # fail-open: emit failure never breaks the gate

        # task_metadata_snapshot seam — per-write mirror, TaskCreate leg. A
        # created task whose initial metadata carries a targeted key
        # (PER_WRITE_MIRROR_KEYS) mirrors the whole payload immediately —
        # the create IS the first write of the open-task-consumed class.
        # SIBLING of the dispatch_variety block above, deliberately NOT
        # nested under its is_lead gate: this leg carries its own frame
        # gate (canonical-journal-frame) because targeted keys include
        # teammate-written classes. The platform-assigned id exists only in
        # the create response, so resolution goes through _created_task_id;
        # a missing id skips the emit (coverage degrades by one write,
        # never breaks the gate — the dispatch_variety posture). The
        # helper's own read_task_json returns the just-created file (the
        # platform write lands before PostToolUse), so subject/owner
        # resolve from disk and the overlay is a near-identity merge.
        if (
            isinstance(incoming_metadata, dict)
            and any(k in PER_WRITE_MIRROR_KEYS for k in incoming_metadata)
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            new_task_id = _created_task_id(tool_response, tool_input)
            if new_task_id:
                _emit_per_write_snapshot(
                    team_name, new_task_id, incoming_metadata
                )

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
            # task_metadata_snapshot seam — lead completion (additive-after;
            # hermetic). Mirrors non-handoff sibling metadata into the
            # journal at the lead's acceptance-commit, the completion-time
            # point that lands BEFORE the boundary-shaped task-store drain
            # that would destroy the source file. The {**disk, **incoming}
            # shallow merge mirrors the platform's shallow metadata-merge
            # semantics and the pass-it-resolved pattern of the handoff
            # backstop below (order-independent: correct whether or not the
            # platform's write has landed at fire time). A None-valued
            # incoming key is the platform's DELETE op (set-to-null removes
            # the key), so the overlay drops it rather than mirroring a
            # phantom null the post-state no longer carries. All eligibility,
            # dedup, and size-bounding live in the substrate; a snapshot
            # failure must never affect the handoff emit above or this
            # hook's advisory evaluation.
            try:
                incoming_md = (
                    incoming_metadata
                    if isinstance(incoming_metadata, dict)
                    else {}
                )
                emit_task_metadata_snapshot(
                    team_name,
                    task_id,
                    subject,
                    owner,
                    {
                        **metadata,
                        **{
                            k: v
                            for k, v in incoming_md.items()
                            if v is not None
                        },
                    },
                )
            except Exception:
                pass

        # artifact_paths emit BACKSTOP (#927 durability nudge). A FAIL-OPEN
        # advisory: when a PREPARE/ARCHITECT phase task completes but the
        # lead-frame emit site forgot to write the durable artifact_paths
        # pointer, nudge so the GC-immune recovery pointer isn't silently
        # missed. The pointer is what survives `git worktree remove` and lets
        # the secretary distill the disk artifact at harvest; without it, that
        # phase degrades to HANDOFF-only recovery (the #927 failure path).
        #
        # is_lead-gated, mirroring the lead-side emit above: only the lead's
        # process resolves the canonical journal via the implicit read, and only
        # the lead is the writer the nudge is aimed at. A teammate self-
        # completion has no populated context and is correctly skipped.
        #
        # This hook canNOT glob (no worktree path in the PostToolUse frame) — it
        # only DETECTS the missing emit (the journal read is path-independent in
        # the lead frame) and nudges; it never blocks the TaskUpdate. EXEMPT:
        #   - CODE:/TEST:/ATOMIZE:/CONSOLIDATE:/other subjects → not in the
        #     phase→workflow map → _phase_artifact_requirement returns None →
        #     silent (their artifacts are git-tracked or non-existent).
        #   - skipped phases (metadata.skipped is exactly True) → no artifact
        #     produced. The guard uses a strict `is not True` identity check, so
        #     ONLY the boolean True exempts; a truthy-but-non-True value
        #     (1, "yes", etc.) does NOT count as skipped and the backstop still
        #     fires — fail-safe toward nudging on an ambiguous skip marker.
        # Emit ordering is satisfied by construction: the emit site fires at
        # phase-output validation BEFORE the lead completes the phase task, so by
        # this PostToolUse(completed) the event is already on disk (a synchronous
        # journal append). Fail-open covers any residual race.
        if pact_context.is_lead(input_data) and metadata.get("skipped") is not True:
            requirement = _phase_artifact_requirement(subject)
            if requirement is not None:
                workflow, feature = requirement
                if not _artifact_paths_event_present(workflow, feature):
                    advisories.append((
                        "artifact_paths_emit_missing",
                        f"PACT task_lifecycle_gate: phase Task {task_id} "
                        f"({subject!r}) completed without an artifact_paths "
                        f"journal event for (workflow={workflow!r}, "
                        f"feature={feature!r}). The lead-frame emit (glob the "
                        f"phase's docs/ output + write --type artifact_paths) "
                        "appears to have been skipped — the GC-durable recovery "
                        "pointer is missing, so this phase's disk artifact will "
                        "degrade to HANDOFF-only at harvest. Emit the "
                        "artifact_paths event for this phase before the worktree "
                        "is torn down. Advisory only — not blocking.",
                    ))

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
                    # Echo the FULL canonical schema (every field, with the
                    # variety_acknowledgment-is-an-OBJECT note) after the
                    # specific problem, so the relayed rejection covers ANY
                    # schema-invalid sub-reason — missing field, empty/non-string
                    # field, OR a malformed variety_acknowledgment (e.g. a
                    # free-text string) — in one read. This is the LEAD-SIDE
                    # completion advisory: the teammate receives the schema when
                    # the lead relays the rejection, not at their own write
                    # moment. Appended at this single advisory site (not per
                    # validator branch) because all sub-reasons funnel into this
                    # one `teachback_submit_schema_invalid` advisory;
                    # TEACHBACK_SCHEMA_ECHO is the DRY schema-derived string.
                    advisories.append((
                        "teachback_submit_schema_invalid",
                        f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                        f"{schema_problem}. {TEACHBACK_SCHEMA_ECHO}",
                    ))

            # #955 teachback_ack emit — GC-immune mirror of the teammate's
            # variety_acknowledgment, emitted at the lead's TaskUpdate(A,
            # completed) accepting the teachback. The ack lives on the DISK
            # Task-A (the teammate wrote teachback_submit earlier; the lead's
            # accept TaskUpdate carries only status), so read it from `metadata`
            # (the on-disk post-state resolved above). is_lead-gated (mirrors b2):
            # only the lead's process resolves the canonical journal; a teammate
            # frame self-drops (#877). Best-effort: any malformed/absent ack or an
            # append failure skips the emit without breaking the gate.
            if pact_context.is_lead(input_data) and isinstance(teachback_submit, dict):
                ack = teachback_submit.get("variety_acknowledgment")
                if isinstance(ack, dict):
                    flag = ack.get("rationale_articulates_this_dispatch")
                    if isinstance(flag, str) and flag:
                        ack_fields = {
                            "task_id": str(task_id),
                            "rationale_articulates_this_dispatch": flag,
                        }
                        concern = ack.get("concern")
                        if isinstance(concern, str) and concern.strip():
                            ack_fields["concern"] = concern  # optional field
                        # Append-only, no dedup by design: the lead's accepting
                        # TaskUpdate(A, status="completed") fires naturally-once
                        # per teachback acceptance, so no occupant marker is
                        # needed (cf. the dispatch_variety note above).
                        try:
                            append_event(make_event("teachback_ack", **ack_fields))
                        except Exception:
                            pass  # fail-open

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

        # #956 write-time BACKSTOP — re-emit agent_handoff when a metadata-only
        # TaskUpdate SETS handoff on an ALREADY-completed task. b1 (TaskCompleted-
        # keyed) and b2 (lead-side at the completing TaskUpdate) both gate on
        # metadata.handoff PRESENCE at completion time, so a handoff written
        # AFTER completion is observed by NEITHER — the residual write-after-
        # completion race. This ④ block (status != "completed") is exactly where
        # that later metadata-only write lands (D4 — runtime-confirmed: a
        # metadata-only TaskUpdate setting handoff reaches this block). Re-call
        # the SAME b2 emitter; _emit_lead_side_agent_handoff owns all eligibility
        # (owner / not-teachback / not-signal / handoff-is-dict) AND the shared
        # O_EXCL occupant marker, so this re-fire is idempotent — if b1/b2 already
        # emitted, already_emitted() short-circuits it to a safe no-op. No new
        # eligibility logic here; the backstop's only job is to CALL the emitter
        # on the write event b1/b2 structurally cannot see.
        #
        # is_lead-gated (mirrors b2 at the completion surface): only the lead's
        # process resolves the canonical journal; a teammate frame self-drops
        # (#877). The in-process/lead branch is the fail-safe default.
        #
        # ACCEPTED RESIDUAL: if this fires-open on a hard crash AND the handoff
        # is then never set in a later TaskUpdate, exactly one agent_handoff
        # journal event is lost — recoverable out-of-band (git) when the work
        # was committed. This is the deliberate posture: the nudge gate is
        # advisory (never blocks), the backstop is the guarantee, and a
        # journal-emit failure must never break completion. One lost durable
        # record is strictly better than a stranded completion.
        if (
            pact_context.is_lead(input_data)
            and isinstance(incoming_handoff, dict)
            and incoming_handoff
            and isinstance(task_a, dict)
            and task_a
            and task_a.get("status") == "completed"
        ):
            owner_bs = task_a.get("owner") or ""
            subject_bs = task_a.get("subject") or ""
            # Pass the incoming handoff merged onto the on-disk metadata view so
            # the emitter sees it explicitly (order-independent: the platform's
            # synchronous metadata write has landed by PostToolUse, so the
            # on-disk handoff IS the incoming handoff — but pass it resolved to
            # stay correct regardless of write-ordering). _emit_lead_side_agent_handoff
            # re-validates owner/teachback/signal/handoff-dict + dedups.
            meta_bs = task_a.get("metadata") if isinstance(task_a.get("metadata"), dict) else {}
            meta_for_emit = {**meta_bs, "handoff": incoming_handoff}
            _emit_lead_side_agent_handoff(
                team_name, task_id, owner_bs, subject_bs, meta_for_emit
            )

        # task_metadata_snapshot seam — post-completion backstop (hermetic).
        # GENERALIZES the handoff backstop above from handoff-only to
        # any-metadata: a metadata-only TaskUpdate landing on an ALREADY-
        # completed task (late verification records, a superseding analysis
        # write) is observed by neither completion-time seam, so it is
        # mirrored here. Kept a SIBLING block of the handoff backstop — the
        # fire predicates differ by design (handoff-dict vs any-metadata);
        # do not merge the conditions. Content-key dedup in the substrate
        # makes the re-fire idempotent: an unchanged payload no-ops, a
        # changed one emits a superseding event readers resolve latest-ts.
        # A None-valued incoming key is the platform's DELETE op (set-to-null
        # removes the key), so the overlay drops it rather than mirroring a
        # phantom null the disk state no longer carries; a delete-ONLY write
        # therefore mirrors the disk state as-is (deduping if unchanged).
        # is_lead-gated like every lead-frame emit: only the lead's process
        # resolves the canonical journal; a teammate frame self-drops.
        if (
            pact_context.is_lead(input_data)
            and isinstance(incoming_metadata, dict)
            and incoming_metadata
            and isinstance(task_a, dict)
            and task_a
            and task_a.get("status") == "completed"
        ):
            try:
                disk_md = (
                    task_a.get("metadata")
                    if isinstance(task_a.get("metadata"), dict)
                    else {}
                )
                emit_task_metadata_snapshot(
                    team_name,
                    task_id,
                    task_a.get("subject") or "",
                    task_a.get("owner"),
                    {
                        **disk_md,
                        **{
                            k: v
                            for k, v in incoming_metadata.items()
                            if v is not None
                        },
                    },
                )
            except Exception:
                pass

        # task_metadata_snapshot seam — OPEN-task per-write mirror. A
        # metadata write carrying a targeted key (PER_WRITE_MIRROR_KEYS:
        # the open-task-consumed class that a status-blind whole-store
        # drain destroys while load-bearing) mirrors the whole overlay
        # immediately, instead of waiting for a completion-time seam that
        # a drained task never reaches. SIBLING of the post-completion
        # backstop above — disjoint by construction on the same
        # task_a.status read (backstop fires == "completed", this leg
        # fires otherwise); do not merge the conditions. A COMPLETING
        # TaskUpdate carrying targeted keys never lands here (status ==
        # "completed" routes to the completion block, whose lead-completion
        # seam mirrors the same overlay — no double-fire). Missing task
        # file / unknown status is treated as open → fire: an over-fire of
        # already-mirrored content dedups to a no-op on the shared
        # content-hash marker, while a skipped fire is a durability hole —
        # over-firing is self-correcting, under-firing is not.
        #
        # Frame gate is is_canonical_journal_frame, NOT is_lead: targeted
        # keys include teammate-written ones (teachback_submit), and the
        # in-process teammate frame writes the canonical journal (one
        # process, one session). A tmux teammate frame skips — emitting
        # there could silo the event AND poison the shared content-hash
        # marker namespace. Conjunction order is deliberate (perf):
        # ns-scale registry scan first, already-paid disk-status read
        # second, the predicate's config.json read LAST (paid only on
        # matched non-lead frames — rare by construction).
        if (
            isinstance(incoming_metadata, dict)
            and any(k in PER_WRITE_MIRROR_KEYS for k in incoming_metadata)
            and (
                task_a.get("status") != "completed"
                if isinstance(task_a, dict)
                else True
            )
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            _emit_per_write_snapshot(
                team_name, task_id, incoming_metadata, task=task_a
            )

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
        input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
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
