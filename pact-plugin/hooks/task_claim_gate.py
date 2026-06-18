#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_claim_gate.py
Summary: Teammate-side PreToolUse gate (matcher="Edit|Write|Bash") that closes
         the "Task B owned-but-pending while work is live" gap mechanically
         (#961 Cycle 2; defense-in-depth behind the Cycle-1 prose fix). When a
         teammate begins implementation work (Edit/Write/Bash) but has not yet
         flipped its pre-assigned, just-unblocked Task B from `pending` to
         `in_progress`, the gate nudges (M1) and, in the tmux topology with a
         registry-confident identity + exactly one candidate, auto-claims it
         (M2). Advisory only (additionalContext); NEVER denies.
Used by: hooks.json PreToolUse hook (matcher="Edit|Write|Bash")

WHY THIS GATE (the #961 backstop): the orchestrator dispatches Task B
pre-assigned (owner set) but `pending`, so the TEAMMATE flips it to
`in_progress` — preserving the lead's "work started" signal. The Cycle-1 prose
fix asks the teammate to claim-before-work; this hook is the mechanical
defense-in-depth for when the prose is missed.

THREE BEHAVIORAL TIERS, keyed on a STRUCTURAL signal (never a mode flag):
  • Lead frame            — is_lead(stdin) (agent_type ∈ LEAD_AGENT_TYPES) → NO-OP
                            (cheapest early-exit; the highest-frequency actor).
  • Teammate, in-process  — session_id == leadSessionId → identity collapses on
                            the shared session_id (the registered NAME last-wins-
                            collapses), so the gate CANNOT attribute a specific
                            owner → generic, attribution-free advisory only,
                            F3 relevance-guarded; NEVER auto-flips.
  • Teammate, tmux        — session_id != leadSessionId → the distinct session_id
                            disambiguates identity via the registry → enforce:
                            M1 advisory floor / M2 conditional auto-heal of the
                            single owned-unblocked-pending task.

M1 + M2 (both implemented; the split is the single-candidate branch):
  • M1 (advisory floor)   — complete, working, fail-open, never-deny advisory
                            gate (is_lead/topology/mine-filter/F1/F2/F3 + the
                            three advisory shapes).
  • M2 (auto-heal)        — the tmux single-candidate path attempts a direct
                            atomic `pending → in_progress` write of the task JSON
                            (_atomic_claim), falling back to the M1 nudge on any
                            write failure.

POSTURE — FAIL-OPEN, NEVER DENY: every exception or unresolved precondition →
suppress + exit 0. Module-load failure → suppress + exit 0. This gate never
emits permissionDecision:"deny" / exit 2. A fail-CLOSED gate on the
high-frequency Edit|Write|Bash matcher would brick the session, so the posture
is fail-open on EVERY path (a crashed PreToolUse hook is itself non-blocking on
the platform — the explicit catches just keep the exit code clean and the
output well-formed).

IDENTITY IS COORDINATION, NOT AUTHORIZATION: the registry identity and is_lead
are coordination signals only. The registry value is self-asserted/forgeable
(labeling-only per session_registry's trust-boundary docstring); this resolution
MUST NOT leak into any authz/trust predicate.

FORGE INVARIANT (canonical; mirrored verbatim in the trust-partition test
comment): The auto-flip targets a task whose owner == the registry-resolved name.
Identity is COORDINATION-ONLY (the registry is forgeable/labeling). A forged or
last-wins-collapsed registry entry could resolve to a DIFFERENT member's name, so
the flip is NOT guaranteed to act on the acting teammate's OWN task. No-escalation
holds NOT via 'own-task-only' but because the same OS user already has full
TaskUpdate/FS access and the only mutation is a benign pending→in_progress flip —
the gate crosses no privilege boundary.

THREAT-MODEL BOUNDARY (security F-2): every no-escalation claim above rests on a
single assumption — ALL team frames (lead, in-process teammate, tmux teammate)
run as the SAME OS user, who ALREADY holds unrestricted TaskUpdate + filesystem
access to ~/.claude/tasks/. The gate therefore grants no capability the actor
lacks; its only mutation is one that actor could already perform directly. This
guarantee does NOT extend to a multi-user / cross-UID model: if a DIFFERENT OS
user could write ~/.claude/tasks/ or forge the registry, the "benign own-flip"
framing would no longer bound the blast radius and these coordination signals
would need a real authz boundary. PACT is same-OS-user-only; this note marks the
assumption EXPLICITLY so a future multi-user deployment re-evaluates it rather
than inheriting it silently.

ADVISORY-CHANNEL CAVEAT (known, unresolved platform uncertainty): whether
PreToolUse additionalContext reliably reaches the model is an open question in
this repo. The advisory-only paths (in-process / identity-unconfident / multi-
candidate) inherit that uncertainty; they are the SOFTER backstop, with the
Cycle-1 prose layer as the load-bearing universal fix. M2's auto-heal escapes
the uncertainty entirely on the path that matters most (tmux): it mutates the
task JSON directly, so the flip lands whether or not the advisory surfaces.

Input: JSON from stdin with tool_name, tool_input, agent_type, session_id, etc.
Output: JSON with hookSpecificOutput.additionalContext (advisory case) or
        {"suppressOutput": true} (NO-OP / passthrough). ALWAYS exit 0.
"""

from __future__ import annotations

# ─── stdlib first (used on the input-side fail-open BEFORE wrapped imports) ──
import json
import os
import sys
from pathlib import Path

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Cap on the stdin read. Real PreToolUse Edit|Write|Bash frames carry a small
# tool_input and stay well under this; an over-cap frame truncates mid-JSON →
# JSONDecodeError → input-side fail-open. Bounds memory only; does not reject
# sub-cap input. Mirrors the sibling gates' 8 MB cap.
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


# ─── fail-OPEN wrapper on cross-package imports ──────────────────────────────
# This gate must NEVER deny. If an import below raises, we suppress + exit 0
# (fail-open) rather than emitting a deny — unlike the fail-CLOSED deny gates
# (bootstrap_gate / pin_*_gate). A crashed hook (exit 1) is ALSO non-blocking on
# PreToolUse, so even an un-caught raise degrades to fail-open; the explicit
# catch keeps the exit code clean (0) and the output well-formed.
try:
    import shared.pact_context as pact_context
    from shared.session_registry import resolve as registry_resolve
    from shared.task_utils import (
        iter_team_task_jsons,
        is_teachback_subject,
        read_task_json,
    )
    from shared.intentional_wait import is_self_complete_exempt
    from shared.session_state import is_safe_path_component
    from shared.agent_handoff_marker import sanitize_path_component
    from shared.paths import get_claude_config_dir
    _IMPORTS_OK = True
except BaseException:  # noqa: BLE001 — fail-OPEN catch-all (this gate never denies)
    _IMPORTS_OK = False


# ─── advisory copy (semantics fixed by the spec; wording refined here) ───────
_NUDGE_PREFIX = "PACT task_claim_gate: "

# Generic, attribution-free nudge — emitted when identity is UNCONFIDENT
# (in-process collapse OR tmux registry-miss): we know SOMETHING is claimable
# (F3) but cannot attribute which task is the actor's, so we never name an id.
_GENERIC_CLAIM_NUDGE = (
    _NUDGE_PREFIX
    + "If you have a pre-assigned Task B that is still `pending`, claim it first "
    "— `TaskUpdate(<id>, status=in_progress)` — before implementation work, so "
    "the lead's work-started signal stays accurate."
)


def _claim_nudge_single(task_id: str) -> str:
    """Task-specific nudge: identity is confident and exactly one owned task is
    claimable. Names the id; the teammate flips it (M1) — M2 may auto-flip."""
    return (
        _NUDGE_PREFIX
        + f"Your pre-assigned Task #{task_id} is still `pending` while you are "
        f"doing implementation work. Claim it first — "
        f"`TaskUpdate({task_id}, status=in_progress)` — so the lead's "
        "work-started signal stays accurate."
    )


def _claim_nudge_multi(task_ids: list[str]) -> str:
    """Task-specific list nudge: identity is confident but MORE THAN ONE owned
    task is claimable. Lists the ids; the teammate picks. NEVER auto-flips —
    the gate must not guess which task the actor is working on."""
    ids = ", ".join(f"#{tid}" for tid in task_ids)
    return (
        _NUDGE_PREFIX
        + f"You own multiple unblocked `pending` tasks ({ids}) while doing "
        "implementation work. Claim the one you are working on — "
        "`TaskUpdate(<id>, status=in_progress)` — before continuing, so the "
        "lead's work-started signal stays accurate."
    )


def _auto_claimed_note(task_id: str) -> str:
    """Transparency note emitted after a successful M2 auto-claim. Non-deny."""
    return (
        _NUDGE_PREFIX
        + f"Auto-claimed your pre-assigned Task #{task_id} "
        "(`pending → in_progress`) to preserve the lead's work-started "
        "signal. No action needed."
    )


# ─── topology / plumbing helpers ─────────────────────────────────────────────


def _split_name_team(resolved: object) -> "tuple[str | None, str | None]":
    """Split a registry `resolve()` value into (name, team).

    Returns (None, None) on any miss/malformed value — a None name means
    identity is UNCONFIDENT and the gate must NEVER guess a typed owner (F2).
    """
    if not isinstance(resolved, str) or "@" not in resolved:
        return (None, None)
    name, _, team = resolved.partition("@")
    if not name or not team:
        return (None, None)
    return (name, team)


def _stdin_team(stdin: dict) -> str:
    """Last-resort team_name fallback: a stdin-provided team_name (often ABSENT
    on tmux PreToolUse frames). Returns "" when missing/non-string."""
    team = stdin.get("team_name")
    return team if isinstance(team, str) else ""


def _read_lead_session_id(team_name: str, teams_dir: str | None = None) -> str:
    """Read the top-level ``leadSessionId`` from
    ``~/.claude/teams/{team_name}/config.json``.

    Mirrors the guarded-read shape of ``pact_context._iter_members``
    (try/except → default) — net-new read; no hook reads ``leadSessionId``
    today. Returns "" on any of: unsafe team_name, missing/unreadable file,
    malformed JSON, non-object top-level, or a missing/non-string key. An empty
    return routes the caller to the fail-safe in-process/NO-OP default. Never
    raises.

    CURRENCY DEPENDENCY (the in-process/tmux topology compare rests on this):
    in-process safety assumes ``config.leadSessionId`` is CURRENT. A STALE value
    — e.g. after a session resume where the team config retains a prior session's
    id — could make the caller's ``session_id == leadSessionId`` compare
    MISCLASSIFY an in-process frame as tmux (or vice-versa). Blast radius is
    BOUNDED and benign: the discriminator is coordination-only (all frames are the
    same OS user; no privilege boundary), and the worst misclassification outcome
    is a benign ``pending → in_progress`` auto-flip of the teammate's OWN
    owned-unblocked-pending task (in-process misread as tmux) — the very action
    the teammate was supposed to take — or a missed nudge (tmux misread as
    in-process). The ``owner == confident_name`` conjunction still bounds it to
    the resolved teammate's task; no wrong-teammate flip, no escalation.
    """
    if not is_safe_path_component(team_name):
        return ""
    if teams_dir:
        config_path = Path(teams_dir) / team_name / "config.json"
    else:
        config_path = get_claude_config_dir() / "teams" / team_name / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        lead_session_id = data.get("leadSessionId")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return ""
    return lead_session_id if isinstance(lead_session_id, str) else ""


# ─── claim-candidate predicates ──────────────────────────────────────────────


def _is_unblocked(task: dict, by_id: dict) -> bool:
    """§7 CORRECTED unblocked predicate: every ``blockedBy`` id resolves to a
    ``completed`` task.

    NOT "blockedBy empty" — the platform RETAINS completed-blocker ids in the
    raw ``blockedBy`` list, and this hook reads raw JSON. Under a literal
    "empty" filter, a just-unblocked Task B (whose sole blocker — the teachback
    Task A — is now completed) would still carry ``blockedBy=[A]`` and be
    misclassified as blocked, making the gate a silent no-op on its own target
    scenario.

    An unresolvable blocker (id with no matching task in ``by_id`` — deleted or
    cross-team) is treated as RESOLVED (no live dependency): permissive per the
    spec, bounded by the single-candidate + owned + pending conjunction at the
    call site.
    """
    for bid in (task.get("blockedBy") or []):
        blocker = by_id.get(str(bid))
        if blocker is None:
            continue  # unresolvable/deleted blocker → treated as resolved (§7)
        if blocker.get("status") != "completed":
            return False  # an open dependency remains
    return True


def _any_unclaimed_claim_candidate(tasks: list, by_id: dict, team_name: str) -> bool:
    """F3 relevance-guard for the attribution-free generic advisory.

    The in-process and identity-unconfident branches cannot attribute a specific
    owner, so the generic nudge must not fire on every Edit/Write/Bash. Emit
    ONLY if the team has ≥1 task that is `pending` AND unblocked AND
    not-teachback-subject AND not-self-complete-exempt AND has a NON-EMPTY owner
    (someone's pre-assigned Task B still pending). Otherwise NO-OP.
    """
    for task in tasks:
        owner = task.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            continue  # must be a pre-assigned (owned) task
        if task.get("status") != "pending":
            continue
        if not _is_unblocked(task, by_id):
            continue
        if is_teachback_subject(task.get("subject") or ""):
            continue
        if is_self_complete_exempt(task, team_name):
            continue
        return True
    return False


# ─── M2 atomic auto-heal write (mirrors task_lifecycle_gate._writeback_dispute) ─


def _atomic_claim(task_id: str, team_name: str) -> bool:
    """M2 auto-heal: flip a single owned-unblocked-pending task
    ``pending → in_progress`` via a direct, atomic filesystem write of the WHOLE
    task JSON.

    Mirrors ``task_lifecycle_gate._writeback_dispute`` in shape: read the whole
    dict via ``read_task_json`` (path-traversal safe) → RE-VALIDATE
    ``status == "pending"`` under the read-back (the load-bearing no-clobber
    guard against a race between the scan and this write) → flip the TOP-LEVEL
    status → write the WHOLE json via ``.tmp`` + ``os.replace`` (atomic;
    preserves every sibling top-level key — the shallow-merge pin).

    Fail-OPEN: any failure (unsafe ids, missing task, status moved, OSError)
    returns False and the caller degrades to the CLAIM_NUDGE_SINGLE advisory.
    The flip — not the advisory — is the load-bearing signal: it lands whether
    or not the advisory channel surfaces (the M2 robustness argument). Never
    raises.

    CONCURRENCY — ACCEPTED TOCTOU WINDOW (issue #968): this is a LOCK-FREE
    read-modify-write. The ``status == "pending"`` re-validation below reads at
    ``read_task_json`` time, NOT atomically with the ``os.replace``; in the
    read→replace window a concurrent PLATFORM write to the SAME task can be lost
    (the whole-file replace is last-writer-wins, so a racing platform write — e.g.
    the lead flipping ``status`` to ``completed`` — would be reverted). NO
    plugin-side ``fcntl.flock`` is taken to close this, and that is DELIBERATE:
    the #34 probe established that the platform does NOT ``flock`` the
    ``{team}/.lock`` when writing tasks (5.2M non-blocking samples, 0 EWOULDBLOCK
    across 8 confirmed in-window platform writes; the ``.lock`` is universally
    0 bytes; the platform relies on atomic ``os.replace``). A plugin flock would
    serialize against nothing — FALSE SAFETY, not real mutual exclusion. The REAL
    safety is two-fold and sufficient in practice: the atomic ``os.replace`` (no
    torn write — a reader always sees the whole old or whole new file) plus the
    ``status == "pending"`` no-clobber re-validation (which catches the COMMON
    ordering, where the competing write lands before our read). The residual
    read→replace window is ACCEPTED: low-probability (the lead completes Task B
    only AFTER the teammate's HANDOFF + idle, never while the teammate is
    mid-Edit/Write/Bash — the only moment this flip fires), recoverable (a later
    platform read-modify-write heals it), and at PARITY with the sibling
    ``_writeback_dispute`` / ``_writeback_audit_recovery`` lock-free writebacks.
    Tracked as FUTURE in issue #968; the only complete fix — one real lock shared
    across all three writebacks — awaits a platform-cooperative lock primitive
    that does not exist today.

    Returns True iff the flip was written.
    """
    # sanitize_path_component strips C0/\x00 + path-traversal fragments; an empty
    # result (e.g. a pure-traversal id) → abort. Mirrors _writeback_dispute's
    # NUL-byte defense (a bare re.sub would let \x00 reach .exists() → ValueError).
    sanitized = sanitize_path_component(task_id)
    if not sanitized:
        return False
    # team_name was already validated in _evaluate (Step 2); re-check here as
    # defense-in-depth for any future caller — via the CANONICAL
    # is_safe_path_component (the SSOT allowlist SAFE_PATH_COMPONENT_RE =
    # [A-Za-z0-9_-]+) instead of a duplicated inline regex. DRY: one validator,
    # behaviorally identical to the prior re.fullmatch (verified 0-divergence).
    if not is_safe_path_component(team_name):
        return False

    task = read_task_json(sanitized, team_name)
    if not task:
        return False

    # No-clobber re-validation: another actor may have moved the task between the
    # scan and now. Only flip a still-pending task; never overwrite a status
    # another actor already changed. (owner / unblocked were validated pre-scan;
    # this status re-check is the load-bearing race guard.)
    if task.get("status") != "pending":
        return False

    task["status"] = "in_progress"  # TOP-LEVEL flip (the case M0 proved honored)
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        task["metadata"] = metadata
    # gate_writeback: set for convention-parity with the _writeback_* recursion
    # guard, but NON-LOAD-BEARING on this path. This is a direct FS write that
    # emits NO TaskUpdate event, and the ONLY reader of gate_writeback
    # (task_lifecycle_gate.py) inspects an INCOMING TaskUpdate's
    # tool_input.metadata — never on-disk task metadata — so it cannot recursively
    # re-fire any gate here. Set as belt-and-suspenders / future-proofing against
    # any future replay path; do NOT mistake it for a load-bearing recursion guard
    # for the status flip.
    metadata["gate_writeback"] = True

    # Writer/reader symlink-anchoring parity: mirror the sibling READERS'
    # resolve()+relative_to containment assertion (read_task_json does
    # `task_dir.resolve().relative_to(base.resolve())`; iter_team_task_jsons
    # asserts the resolved team dir under the resolved tasks_root) BEFORE writing.
    # team_name already passed is_safe_path_component (no traversal), but a
    # SYMLINK at tasks/{team_name} pointing outside base is an ORTHOGONAL escape
    # vector the readers guard and the writer must too — else os.replace could
    # land the write outside the anchored tasks tree. Fail-OPEN on mismatch:
    # return False → caller degrades to the M1 advisory (never deny, never write
    # outside the anchored dir). Write via the UNresolved path after the check
    # passes (matches read_task_json; inherits the same accepted TOCTOU window —
    # parity, not gold-plating).
    base = get_claude_config_dir() / "tasks"
    tasks_root = base / team_name
    try:
        tasks_root.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        return False
    target = tasks_root / f"{sanitized}.json"
    tmp = tasks_root / f".{sanitized}.json.tmp"
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


# ─── core decision (pure-ish read; M2 adds one conditional write) ────────────


def _evaluate(stdin: dict) -> str | None:
    """Return the advisory string to surface, or None for NO-OP.

    Every step is fail-open: any exception or unresolved precondition returns
    None (the caller suppresses + exits 0). The ONLY mutation is the M2
    auto-heal write on the tmux, identity-confident, exactly-one-candidate
    branch (_atomic_claim); every other path is read-only.
    """
    # ── Step 0: role early-exit (cheapest; covers the highest-frequency actor) ─
    if pact_context.is_lead(stdin):
        return None  # lead NO-OP, both modes

    session_id = stdin.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None  # fail-safe

    # ── Step 1: identity + team via the registry-DIRECT primitive (F2) ────────
    # resolve() is members[]-validated, self-lookup only, never raises. A None
    # name → identity UNCONFIDENT → advisory, NEVER a typed guess (no
    # resolve_agent_name type-strip fallback).
    confident_name, team_from_registry = _split_name_team(registry_resolve(session_id))

    # ── Step 2: team_name (registry @team half wins; context/stdin fallbacks) ─
    # The @team half survives the in-process name-collapse, so it is the primary
    # team source in BOTH modes; the context-file + stdin fallbacks cover a
    # registry miss.
    team_name = team_from_registry
    if not team_name:
        try:
            pact_context.init(stdin)
        except Exception:
            pass
        team_name = pact_context.get_team_name() or _stdin_team(stdin)
    if not team_name or not is_safe_path_component(team_name):
        return None  # cannot locate config → fail-safe NO-OP

    # ── Step 3: topology discriminator (the final D3 signal — a config read) ──
    lead_session_id = _read_lead_session_id(team_name)
    if not lead_session_id:
        # ASYMMETRY (design note): an UNKNOWN topology (config unreadable / no
        # leadSessionId) NO-OPs SILENTLY — no advisory at all — whereas a
        # CONFIRMED in-process topology DOES emit the generic advisory (below).
        # Both are "cannot attribute a specific owner" states, yet they diverge by
        # design: unknown-topology means we cannot even confirm we are a teammate
        # in a known team, so the most conservative fail-safe (total NO-OP) is
        # correct; confirmed-in-process is a KNOWN, safe state where the
        # attribution-free nudge is warranted. The extra suppression on the
        # unknown path is intentional, not an oversight.
        return None  # unknown topology → fail-safe total NO-OP (no advisory)
    in_process = session_id == lead_session_id

    # ── Step 4: load the team task set ONCE (also feeds the unblocked predicate) ─
    tasks = list(iter_team_task_jsons(team_name))
    by_id = {str(t.get("id")): t for t in tasks if t.get("id") is not None}

    if in_process:
        # IN-PROCESS: identity collapsed → generic non-mutating advisory only,
        # F3 relevance-guarded. NEVER auto-flip.
        if _any_unclaimed_claim_candidate(tasks, by_id, team_name):
            return _GENERIC_CLAIM_NUDGE
        return None

    # ── TMUX branch: enforce ──────────────────────────────────────────────────
    if not confident_name:
        # Identity UNCONFIDENT (registry miss / unregistered teammate). NEVER
        # guess a typed owner; fall back to the generic, attribution-free nudge.
        if _any_unclaimed_claim_candidate(tasks, by_id, team_name):
            return _GENERIC_CLAIM_NUDGE
        return None

    mine = [
        t
        for t in tasks
        if t.get("owner") == confident_name
        and t.get("status") == "pending"
        and _is_unblocked(t, by_id)  # §7 — all blockers completed
        and not is_teachback_subject(t.get("subject") or "")
        and not is_self_complete_exempt(t, team_name)
    ]

    # F1 idempotency: nothing claimable → NO-OP. After a flip the task is
    # in_progress (not pending) → never re-nagged, never re-flipped.
    if not mine:
        return None

    if len(mine) == 1:
        # Identity-confident + exactly one candidate → M2 auto-heal: attempt the
        # atomic pending→in_progress flip. On any write failure (_atomic_claim
        # returns False — incl. the no-clobber re-validation aborting) degrade to
        # the advisory (fail-open, never deny).
        task_id = str(mine[0].get("id"))
        if _atomic_claim(task_id, team_name):
            return _auto_claimed_note(task_id)
        return _claim_nudge_single(task_id)

    # len(mine) > 1: multiple owned-unblocked-pending → list, NEVER guess.
    return _claim_nudge_multi([str(t.get("id")) for t in mine])


def main() -> None:
    # Input-side fail-open: an unreadable / oversized / malformed stdin frame
    # suppresses + exits 0 (never blocks the tool call).
    try:
        stdin = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Module-load failure or non-dict stdin → suppress + exit 0 (fail-open).
    if not _IMPORTS_OK or not isinstance(stdin, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        advisory = _evaluate(stdin)
    except Exception:
        # Fail-OPEN on any logic error. A gate that bricks Edit/Write/Bash is
        # far worse than a missed nudge. NEVER deny.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if advisory:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",  # MUST be present (#658 invariant)
                "additionalContext": advisory,  # advisory — NOT permissionDecision
            }
        }))
        sys.exit(0)  # exit 0 — advisory, never deny / exit-2

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
