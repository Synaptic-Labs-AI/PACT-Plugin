#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_lifecycle_gate.py
Summary: PostToolUse hook (matcher='TaskCreate|TaskUpdate') enforcing PACT
         lifecycle invariants. Cannot DENY (post-action); emits structural
         advisory via additionalContext, plus a metadata writeback for
         self-completion violations.
Used by: hooks.json PostToolUse matcher='TaskCreate|TaskUpdate' (per the
         unified Task-mutating-tool matcher convention shared with
         wake_lifecycle_emitter and agent_handoff_emitter).

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
  - teachback_addblocks_missing — Teachback Task created without
    addBlocks=[<work_task_id>]
  - work_addblockedby_missing — pact-* work Task created without
    addBlockedBy=[<teachback_id>]
  - completion_no_paired_send — Teachback Task completed without paired
    wake-SendMessage to owner within the configured window
  - handoff_missing — pact-* work Task completed without
    metadata.handoff payload
  - self_completion — Teammate self-completed a Task without carve-out
    → advisory + completion_disputed writeback
  - handoff_schema_invalid — metadata.handoff present but malformed
    (disjoint with handoff_missing)
  - teachback_submit_missing — Teachback Task completed without
    metadata.teachback_submit payload
  - teachback_submit_schema_invalid — metadata.teachback_submit present
    but malformed against the 5-field canonical schema (disjoint with
    teachback_submit_missing)
  - reasoning_reconstruction_missing_at_required_band — Teachback
    submitted at REQUIRED band (Task B variety.total >= 11) without
    reasoning_reconstruction
  - reasoning_reconstruction_band_unresolvable — band traversal failed
    (missing blocks, missing Task B, missing variety); fail-open advisory
    documents the gap without blocking lifecycle
  - variety_missing_on_dispatch_task — pact-* work Task created without
    metadata.variety OR with malformed per-dimension rationales
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

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import os
import sys
from typing import NoReturn


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-advisory (PostToolUse cannot DENY).

    Mirrors bootstrap_gate._emit_load_failure_deny but for PostToolUse —
    advisory output + exit 0 since deny is not a valid PostToolUse verdict.
    Uses ONLY stdlib (json, sys) so it remains functional even when every
    wrapped import below fails.
    """
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"PACT task_lifecycle_gate {stage} failure — lifecycle "
                f"rule enforcement skipped this turn. "
                f"{type(error).__name__}: {error}. Investigate hook "
                "installation and shared module availability."
            ),
        }
    }
    print(json.dumps(output))
    print(
        f"Hook load error (task_lifecycle_gate / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ─────────────────────────
try:
    import re
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    import shared.pact_context as pact_context
    from shared.dispatch_helpers import trustworthy_actor_name
    from shared.intentional_wait import is_self_complete_exempt, is_teachback_exempt
    from shared.session_journal import append_event, make_event
    from shared.task_utils import read_task_json
    from shared.teachback_schema import (
        REASONING_RECONSTRUCTION_REQUIRED_MIN as _REASONING_RECONSTRUCTION_REQUIRED_MIN,
        REQUIRED_FIELDS as _TEACHBACK_REQUIRED_FIELDS,
        REQUIRED_SUBKEYS as _TEACHBACK_REQUIRED_SUBKEYS,
        VARIETY_ACK_VALID_VALUES as _VARIETY_ACK_VALID_VALUES,
        validate_reasoning_reconstruction as _validate_reasoning_reconstruction,
    )
    from shared.tool_response import extract_tool_response
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


# ─── constants ────────────────────────────────────────────────────────────────

# Paired-SendMessage time window (seconds) for the teachback-completion
# rule. Window: 120s — sized to cover normal lead reaction time between
# the teachback-completing TaskUpdate and the paired wake-SendMessage,
# while still detecting genuinely-missing pairs before the teammate's
# idle-poll cycle.
PAIRED_SENDMESSAGE_WINDOW_S = 120

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

# Teachback schema constants (_TEACHBACK_REQUIRED_FIELDS,
# _VARIETY_ACK_VALID_VALUES, _REASONING_RECONSTRUCTION_REQUIRED_MIN) and the
# reasoning_reconstruction validator are imported from shared.teachback_schema
# (SSOT). _TEACHBACK_REQUIRED_SUBKEYS and _validate_reasoning_reconstruction
# are consumed by the write-time advisory rules below.

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


# ─── paired-SendMessage check (for teachback completion) ─────────────────────


def _has_paired_sendmessage(owner: str, window_s: int) -> bool:
    """Return True iff the owner's inbox file shows ANY message from
    team-lead with timestamp within the last `window_s` seconds.

    Inbox path: ~/.claude/teams/{team_name}/inboxes/{owner}.json
    Format: JSON array of {from, text, timestamp, ...}.

    Path-traversal defense: owner is sanitized (alphanumeric, '-', '_' only)
    to prevent escape from the inboxes directory. team_name comes from
    pact_context (harness-set).

    Fail-OPEN: any error reading/parsing returns False (advisory will fire).
    Conservative since the goal is to surface a missing wake-message, not
    suppress on read errors.
    """
    if not owner or not isinstance(owner, str):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", owner):
        return False
    team_name = pact_context.get_team_name() if hasattr(pact_context, "get_team_name") else ""
    if not team_name:
        # Best-effort: read from pact_context cache directly
        try:
            team_name = pact_context.get_pact_context().get("team_name", "")
        except Exception:
            team_name = ""
    if not team_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", team_name):
        return False
    inbox_path = (
        Path.home() / ".claude" / "teams" / team_name / "inboxes" / f"{owner}.json"
    )
    try:
        if not inbox_path.is_file():
            return False
        content = inbox_path.read_text(encoding="utf-8")
        messages = json.loads(content)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(messages, list):
        return False

    now_utc = datetime.now(timezone.utc)
    cutoff_ts = now_utc.timestamp() - window_s
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("from")
        ts_str = msg.get("timestamp")
        if not isinstance(sender, str) or sender != "team-lead":
            continue
        if not isinstance(ts_str, str):
            continue
        try:
            # Accept both Z-suffix and offset forms; datetime.fromisoformat
            # handles offsets directly; replace Z with +00:00 for safety.
            normalized = ts_str.replace("Z", "+00:00")
            ts_dt = datetime.fromisoformat(normalized)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            if ts_dt.timestamp() >= cutoff_ts:
                return True
        except (ValueError, TypeError):
            continue
    return False


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
    sanitized_id = re.sub(r"[/\\]|\.\.", "", task_id)
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

    tasks_root = Path.home() / ".claude" / "tasks" / team_name
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
    if value not in _VARIETY_ACK_VALID_VALUES:
        return (
            f"rationale_articulates_this_dispatch must be one of "
            f"{_VARIETY_ACK_VALID_VALUES}, got {value!r}"
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
    missing = [f for f in _TEACHBACK_REQUIRED_FIELDS if f not in teachback]
    if missing:
        return (
            f"metadata.teachback_submit missing required fields: "
            f"{', '.join(missing)}"
        )
    # Non-empty-string check on the 4 string fields; variety_acknowledgment
    # is a dict, validated by the dedicated sub-validator below.
    string_fields = tuple(
        f for f in _TEACHBACK_REQUIRED_FIELDS if f != "variety_acknowledgment"
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


def _validate_variety_schema(variety: object) -> str | None:
    """Return None if metadata.variety is well-formed per D11, or a short
    reason string. Pure function; never raises.

    Validates the four per-dimension rationale fields (presence +
    non-empty string). Dimension score range checks (1-4) are the
    orchestrator's authority; this hook is defense-in-depth for the
    cargo-cult-prevention property D11 codifies.
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
    return None


def _resolve_required_band_via_blocks(
    task_a: dict, team_name: str
) -> str:
    """Resolve the REQUIRED band for reasoning_reconstruction from Task A
    via blocks traversal to Task B.

    Returns one of:
      - "required": Task B.metadata.variety.total >= 11
      - "recommended": 7 <= total <= 10
      - "skipped": total <= 6
      - "unresolvable": blocks link missing, Task B file missing, or
        variety absent/malformed on Task B (fail-open: caller emits a
        separate band_unresolvable advisory documenting the gap)
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
    total = variety.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        return "unresolvable"
    if total >= _REASONING_RECONSTRUCTION_REQUIRED_MIN:
        return "required"
    if total >= 7:
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

    # ② TaskCreate rules — teachback addBlocks + work-task addBlockedBy
    if tool_name == "TaskCreate":
        subject = (tool_input.get("subject") or "")
        is_teachback = _is_teachback_subject(subject)
        owner = tool_input.get("owner") or ""
        if not isinstance(owner, str):
            owner = ""

        if is_teachback and not tool_input.get("addBlocks"):
            advisories.append((
                "teachback_addblocks_missing",
                "PACT task_lifecycle_gate: Teachback Task created without "
                "addBlocks=[<work_task_id>]. The teachback gate must block "
                "the work task (per pact-completion-authority).",
            ))

        # Clause order is intentional: the cheap checks
        # (is_teachback dict-lookup, owner.startswith string-prefix,
        # tool_input.get dict-lookup) precede the disk-reading
        # is_teachback_exempt predicate. Common path (well-formed
        # dispatch with addBlockedBy provided) short-circuits at the
        # 3rd clause and never hits disk. Failure path (missing
        # addBlockedBy) does hit disk via _iter_members, but this is
        # the rare misconfiguration path — amortized cost near zero.
        if (
            not is_teachback
            and owner.startswith("pact-")
            and not tool_input.get("addBlockedBy")
            and not is_teachback_exempt(owner, team_name)
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
        # two trigger paths (absent variety OR malformed per-dimension
        # rationales); distinct message text per path, same rule name —
        # the lead-side correction is the same (re-stamp variety) in
        # either case. Clause order mirrors L575-580: cheap dict-lookups
        # first, disk-touching exempt-check last.
        if (
            not is_teachback
            and owner.startswith("pact-")
            and not is_teachback_exempt(owner, team_name)
        ):
            incoming_variety = (
                tool_input.get("metadata") or {}
            ).get("variety")
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
                schema_problem = _validate_variety_schema(incoming_variety)
                if schema_problem:
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

        # handoff_missing vs handoff_schema_invalid are disjoint per lead
        # clarification:
        #   handoff missing/empty → handoff_missing advisory, skip schema
        #     check (no payload to validate).
        #   handoff present but schema malformed → handoff_schema_invalid
        #     advisory.
        is_work_task = not is_teachback and owner.startswith("pact-")
        if is_work_task:
            handoff = metadata.get("handoff")
            if not handoff:
                advisories.append((
                    "handoff_missing",
                    f"PACT task_lifecycle_gate: Task {task_id} "
                    f"(owner={owner!r}) marked completed without "
                    "metadata.handoff. HANDOFF synthesis was missed.",
                ))
            else:
                schema_problem = _validate_handoff_schema(handoff)
                if schema_problem:
                    advisories.append((
                        "handoff_schema_invalid",
                        f"PACT task_lifecycle_gate: Task {task_id} "
                        f"metadata.handoff schema is invalid — {schema_problem}.",
                    ))

        # Teachback-subject completion-time checks: teachback_submit presence
        # + schema, then paired wake-SendMessage. R1/R2 are disjoint by the
        # same handoff_missing/handoff_schema_invalid pattern at L607-613:
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

            if not _has_paired_sendmessage(owner, PAIRED_SENDMESSAGE_WINDOW_S):
                advisories.append((
                    "completion_no_paired_send",
                    f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                    f"completed without a paired wake-SendMessage to "
                    f"{owner!r} in the last {PAIRED_SENDMESSAGE_WINDOW_S}s. "
                    "blockedBy is pull-only; teammate idles indefinitely.",
                ))

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
                f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                "placed reasoning_reconstruction inside metadata.handoff. "
                "It belongs at top-level on metadata.teachback_submit. "
                "The handoff has reasoning_chain (sender's view); the "
                "teachback has reasoning_reconstruction (receiver's "
                "reconstruction). See pact-teachback skill Common "
                "mistakes row 2.",
            ))

        if isinstance(incoming_teachback, dict):
            task_id = tool_input.get("taskId", "") or ""
            # Shared task_a disk read — R3, R5, and the 3 new
            # teachback-scoped rules consume this one read (R3 needs
            # blocks for traversal, others only need subject).
            task_a = read_task_json(task_id, team_name) if team_name else {}
            subject = task_a.get("subject") or ""
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
                            f"{_VARIETY_ACK_VALID_VALUES} + concern "
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
                    rr_problem = _validate_reasoning_reconstruction(
                        incoming_teachback["reasoning_reconstruction"]
                    )
                    if rr_problem:
                        advisories.append((
                            "reasoning_reconstruction_subkeys_invalid",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} reasoning_reconstruction "
                            f"rejected — {rr_problem}. Canonical 3 "
                            f"sub-keys are {_TEACHBACK_REQUIRED_SUBKEYS} "
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
                        f"{task_id} cannot resolve variety band — "
                        "Task A.blocks link missing OR Task B file "
                        "missing OR Task B.metadata.variety absent. "
                        "Hook advisory skipped; lead should enforce "
                        "variety stamping via teachback_addblocks_missing "
                        "+ variety_missing_on_dispatch_task upstream.",
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

    pact_context.init(input_data)

    try:
        advisories = evaluate_lifecycle(input_data)
    except Exception as e:  # noqa: BLE001 — runtime catch-all → advisory
        _emit_load_failure_advisory("runtime", e)
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
