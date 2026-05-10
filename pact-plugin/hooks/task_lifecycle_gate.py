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
    from shared.intentional_wait import is_self_complete_exempt
    from shared.session_journal import append_event, make_event
    from shared.task_utils import read_task_json
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

# Required handoff schema fields (advisory if present-but-malformed).
_HANDOFF_REQUIRED_FIELDS = (
    "produced",
    "decisions",
    "reasoning_chain",
    "uncertainty",
    "integration",
    "open_questions",
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
    tool_response = input_data.get("tool_response") or input_data.get("tool_output") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not isinstance(tool_response, dict):
        tool_response = {}

    # ① Recursion guard (self-completion writeback self-trigger): skip
    # silently if THIS update is the gate's own writeback. Checked FIRST
    # before any other rule.
    incoming_metadata = tool_input.get("metadata") or {}
    if isinstance(incoming_metadata, dict) and incoming_metadata.get("gate_writeback") is True:
        return []

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

        if (
            not is_teachback
            and owner.startswith("pact-")
            and not tool_input.get("addBlockedBy")
        ):
            advisories.append((
                "work_addblockedby_missing",
                f"PACT task_lifecycle_gate: pact-* Task created "
                f"(owner={owner!r}) without addBlockedBy=[<teachback_id>]. "
                "Work tasks must block on teachback acceptance.",
            ))

    # ③ TaskUpdate-to-completed rules — paired-send, handoff presence,
    # handoff schema, self-completion
    if tool_name == "TaskUpdate" and tool_input.get("status") == "completed":
        task_id = tool_input.get("taskId", "") or ""
        # Resolve team_name from pact_context once for use by both the
        # disk-fallback read and the self-completion carve-out predicate
        # (is_self_complete_exempt now keys on team-config agentType).
        try:
            team_name = pact_context.get_pact_context().get("team_name", "")
        except Exception:
            team_name = ""
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

        # Teachback completion requires a paired wake-SendMessage to owner
        if is_teachback and owner:
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
