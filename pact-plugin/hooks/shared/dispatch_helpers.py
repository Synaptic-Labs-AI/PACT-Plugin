#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/dispatch_helpers.py
Summary: Shared helpers for #662 dispatch_gate.py and task_lifecycle_gate.py.

Exposes:
  - is_registered_pact_specialist(subagent_type) — registry check (one
    of the dispatch_gate rules; rejects unregistered pact-* spawns)
  - is_pact_specialist_owner(owner, team_name) — bare-owner → team-config
    agentType → registry resolution (one leg of the dispatch recognition)
  - is_owner_bearing_write(tool_input) — pure structural recognition of an
    owner-BEARING TaskUpdate's SHAPE (the dispatch_site population's
    membership leg; broader than the composite wiring shape, because live
    dispatch practice wires owners in split writes)
  - is_owner_wiring_shape(tool_input) — pure structural recognition of the
    COMPOSITE owner-wiring SHAPE (the handoff-ordering gate's "terminal"
    leg; see its docstring for the other legs, why they are deliberately
    NOT folded into one composite, and — important — what this predicate
    does NOT establish)
  - variety_stamp_as_of_write(tool_input, task) — the variety stamp the
    dispatched Task-B WILL carry once this write lands. Models the
    platform's top-level-key REPLACE, not a union of the two sources.
    Shared so the stamp the enforcement gate reads and the stamp the
    dispatch_site emit records can never drift apart.
  - has_task_assigned(team_name, name) — task-assigned check (one of
    the dispatch_gate rules; rejects spawn before TaskCreate)
  - SOLO_EXEMPT — research/exploration agents that bypass dispatch_gate

Module-load discipline (architect §5.6):
  Stdlib imports (json/sys) AND _emit_load_failure_deny defined BEFORE
  the wrapped try/except BaseException block. The wrapped block catches
  cross-package import failures (functools/re/pathlib/shared.pact_context)
  and emits a structured DENY with hookEventName=PreToolUse so any
  importer (dispatch_gate, task_lifecycle_gate) inherits the fail-closed
  contract automatically: if THIS module fails to load, the importer's
  own wrapped import block fires its own _emit_load_failure_deny via the
  re-raised BaseException.
"""

from __future__ import annotations

import json
import sys
from typing import NoReturn


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-closed deny for module-load failure.

    Mirrors PR #660 ``merge_guard_pre._emit_load_failure_deny`` and the
    bootstrap_gate analogue at hooks/bootstrap_gate.py. Uses ONLY stdlib
    (json, sys) so it remains functional even when every wrapped import
    below fails. Audit anchor: hookEventName must be present in any deny
    output (#658 invariant).
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT dispatch_helpers {stage} failure — blocking for safety. "
                f"{type(error).__name__}: {error}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (dispatch_helpers / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── fail-closed wrapper around cross-package imports ─────────────────────
try:
    import functools
    from pathlib import Path
    import shared.pact_context as pact_context
    from shared.task_utils import iter_team_task_jsons
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# Research/exploration agents that legitimately spawn WITHOUT name/team_name
# (per pinned-memory feedback_direct_agent_calls.md). These bypass the
# dispatch_gate entirely. The specialist registry stays simple — these are caught at
# step ① of evaluate_dispatch in dispatch_gate.py.
SOLO_EXEMPT = frozenset({"general-purpose", "Explore", "Plan"})

# ─── specialist registry ───────────────────────────────────────────────────

def _glob_specialists(plugin_root: str) -> frozenset[str]:
    """Glob the specialist stems at ``{plugin_root}/agents/pact-*.md``.

    Uncached, pure-of-cache: the caller supplies plugin_root explicitly. Empty
    plugin_root or missing agents/ directory → empty frozenset (registry
    fail-closed); OSError → empty frozenset. ``pact-orchestrator.md`` IS in the
    glob set; the "orchestrator is the persona, not a dispatchable specialist"
    semantic is enforced at a different layer (system-prompt --agent flag at
    session start), not at the registry.

    Extracted from ``_specialist_registry`` (#878) so the cached registry and
    an explicit-root caller (the session_init startup notice, which runs BEFORE
    the pact_context cache is populated and so must pass an env-resolved root)
    share ONE glob implementation without parameterizing the lru_cache.
    """
    if not plugin_root:
        return frozenset()
    agents_dir = Path(plugin_root) / "agents"
    try:
        return frozenset(p.stem for p in agents_dir.glob("pact-*.md"))
    except OSError:
        return frozenset()


@functools.lru_cache(maxsize=1)
def _specialist_registry() -> frozenset[str]:
    """Glob agents/pact-*.md once per hook subprocess (cached, arg-less).

    Hook subprocesses are short-lived (per-tool-call); the cache is rebuilt
    on every dispatch evaluation. Resolves plugin_root from the pact_context
    cache and delegates the glob to ``_glob_specialists``. Deliberately
    ARG-LESS so the lru_cache holds exactly one entry per subprocess (the hot
    dispatch path); the explicit-root path the startup notice needs pre-cache
    goes through ``_glob_specialists`` directly, never through this cache.
    Empty plugin_root / missing agents/ → empty registry (fail-closed).
    """
    return _glob_specialists(pact_context.get_plugin_root())


def is_registered_pact_specialist(
    subagent_type: str, plugin_root: str = ""
) -> bool:
    """True iff subagent_type matches a file at ``agents/pact-*.md``.

    ``plugin_root`` (#878, additive + backward-compatible): when a non-empty
    plugin_root is passed, resolve the registry against it directly via
    ``_glob_specialists`` (the uncached path) — required by the session_init
    startup notice, which runs BEFORE the pact_context cache is populated, so
    the cached ``_specialist_registry()`` would see an empty plugin_root and
    wrongly report every type as unregistered there. When omitted/empty (every
    existing caller, e.g. the dispatch self-completion gate), fall back to the
    cached ``_specialist_registry()`` — identical behavior to before this param
    existed.
    """
    if not isinstance(subagent_type, str) or not subagent_type:
        return False
    registry = (
        _glob_specialists(plugin_root) if plugin_root else _specialist_registry()
    )
    return subagent_type in registry


def is_pact_specialist_owner(
    owner: str, team_name: str, teams_dir: str | None = None
) -> bool:
    """True iff ``owner`` names a team member whose agentType is a registered
    pact specialist (``agents/pact-*.md``).

    Real task owners are BARE specialist names (``backend-coder``,
    ``test-engineer``), NOT ``pact-``-prefixed — ``pact-*`` is the team-config
    agentType, never the owner. So identifying "this owner is a pact specialist
    teammate" requires resolving the bare owner through team config to its
    agentType (the SAME owner→member→agentType resolution
    ``intentional_wait._is_exempt_agent_type`` uses), then checking registry
    membership. Composes the two existing primitives — ``_iter_members`` (the
    resolution SSOT) + ``is_registered_pact_specialist`` (the registry check) —
    rather than duplicating either.

    Fail-CLOSED to ``False`` on every unresolvable path (empty owner/team_name,
    missing/malformed config, member-not-found, missing/non-str agentType, OR
    any unexpected exception). For the dispatch-variety gate this composes to
    FAIL-OPEN: "not positively a pact specialist" → the gate returns None →
    does not fire, so an unresolvable owner never strands a dispatch.

    The outer bare-except is load-bearing: ``_iter_members`` resolves the
    config dir via ``get_claude_config_dir()`` (→ ``Path.home()``), which can
    raise ``RuntimeError`` that ESCAPES ``_iter_members``'s own typed except —
    so a totally fail-closed helper must catch it here, not rely on a caller's
    outer guard.
    """
    if not isinstance(owner, str) or not owner:
        return False
    if not isinstance(team_name, str) or not team_name:
        return False
    try:
        for member in pact_context._iter_members(team_name, teams_dir):
            if member.get("name") == owner:
                agent_type = member.get("agentType")
                return (
                    isinstance(agent_type, str)
                    and is_registered_pact_specialist(agent_type)
                )
        return False
    except Exception:
        return False  # fail-closed → gate fail-OPEN (never strands a dispatch)


# ─── dispatch-wiring recognition ───────────────────────────────────────────

def is_owner_bearing_write(tool_input: object) -> bool:
    """True iff ``tool_input`` names a non-empty string ``owner`` — the
    owner-BEARING write shape, regardless of ``addBlockedBy``.

    THIS IS THE dispatch_site POPULATION'S MEMBERSHIP LEG. Live dispatch
    practice wires a specialist owner onto a work task in SPLIT writes:
    ``owner`` lands in one TaskUpdate while the blockers landed at creation
    or in a separate update, so the COMPOSITE shape below (owner AND
    addBlockedBy in the same write) never occurs on a real arc — a
    population keyed on it is dark BY CONSTRUCTION, with nothing suppressed
    and no signal that anything was missed. The population leg is therefore
    the broader owner-bearing shape: ANY owner-naming write on the task
    WITNESSES the dispatch.

    RELATION TO ``is_owner_wiring_shape``: the composite is a STRICT SUBSET
    (``is_owner_wiring_shape`` composes this predicate), so the two cannot
    drift on what "owner-bearing" means. They deliberately answer different
    consumers' questions and are deliberately NOT unified — relaxing the
    composite in place would silently change the handoff-ordering gate,
    whose "terminal" recognition requires BOTH halves present. A
    one-expression coupling of the two gates that used to live in the
    composite alone is retired by this split; the ordering gate keeps the
    composite, the population takes the broader leg.

    ONE THING IT DELIBERATELY DOES NOT ESTABLISH: NOT "this is a dispatch."
    The same three further legs the composite's docstring lists still apply
    (specialist-owner resolution, teachback-subject carve-out, exemption
    predicate). For the population consumer the subject carve-out carries a
    NAMED DEPENDENCE: an owner-only write is exactly the split wiring a
    teachback Task-A gate receives, so the SUBJECT carve-out is the sole
    discriminator excluding those gates — the population's membership
    depends on the TEACHBACK subject convention.

    Pure function: no FS, no I/O, never raises. Non-dict input → False.
    """
    if not isinstance(tool_input, dict):
        return False
    owner = tool_input.get("owner")
    return isinstance(owner, str) and bool(owner.strip())


def is_owner_wiring_shape(tool_input: dict) -> bool:
    """True iff ``tool_input`` carries the owner-wiring SHAPE — a non-empty
    string ``owner`` AND a non-empty list ``addBlockedBy`` in the SAME write.

    TWO THINGS IT DELIBERATELY DOES NOT ESTABLISH:

    1. NOT "this is a dispatch." A caller treating True as a dispatch verdict
       will count owner-wiring writes for non-specialist owners and for
       teachback Task-A gates. This is why the name says *shape* and not
       *dispatch*: the verdict needs all three legs below, and this function
       reaches only the first. (It is also why the name avoids the calling
       gate's local term *terminal* — that file defines it as "both halves
       present, as against a partial one-half write", which IS what this
       tests, but the local definition does not travel to a shared module.)
    2. NOT "this is the last such write." The predicate tests ONE
       ``tool_input``; whether a LATER write also carries the composite shape
       is UNMEASURED. The O_EXCL dedup marker — not this predicate — is what
       guarantees one emit per task. Treat dedup as cheap insurance against a
       high-severity failure (a correctly-stamped dispatch reading 0.5) whose
       rate is unmeasured, NOT as something the corpus has shown to occur:
       the repeat-write evidence counts *owner-bearing* writes, most of which
       are PARTIAL and correctly rejected here, and the *composite*-repeat
       rate has never been measured. A consumer needing at-most-once
       semantics must claim the marker rather than infer uniqueness from
       shape; note the "append-only, no dedup by design" comments in the
       lifecycle gate are scoped to ``TaskCreate`` and are a separate
       question from this one.

    The full recognition is three legs, and its SHAPE LEG IS CONSUMER-FORKED:

        handoff_ordering_gate (the "terminal" wiring):
            is_owner_wiring_shape(tool_input)            # this function
        dispatch_site emit (the Q5 population's witness):
            is_owner_bearing_write(tool_input)           # above, broader

        AND is_pact_specialist_owner(owner, team_name)   # above
        AND NOT is_teachback_subject(subject)            # shared.task_utils

    The fork is deliberate and measured, not drift: the workflow templates
    wire Task B with one composite update, but LIVE practice splits the
    write (owner in one update, blockers elsewhere), so a population keyed
    on this composite is dark on every real arc. The ordering gate keeps the
    composite because "terminal" is its question; the population takes the
    broader owner-bearing leg because "was the dispatch witnessed by an
    owner write?" is its question.

    On top of the shape leg, each consumer applies its OWN exemption
    predicate: ``handoff_ordering_gate`` asks "may this owner self-complete?"
    (``is_self_complete_exempt``); the ``dispatch_site`` emit that builds Q5's
    population asks "is this a dispatch requiring understanding verification?"
    (``is_teachback_exempt``). Those two are deliberately NOT unified — see the
    "DO NOT recouple by aliasing to the prior constant" note on
    ``TEACHBACK_EXEMPT_AGENT_TYPES`` in shared/intentional_wait.py. No
    exemption leg belongs in this module.

    WHY THE THREE LEGS ARE NOT FOLDED INTO ONE COMPOSITE: each has a different
    cost. This one is pure and in-memory; ``is_pact_specialist_owner`` reads
    the team config; the subject leg needs a task read. A composite would force
    every caller to read the task before it knows the owner is even a
    specialist, turning a short-circuit into a disk hit. Callers apply the
    legs in their own cost order and share the recognition, not the ordering.

    WHY BOTH FIELDS, AND WHY TOGETHER: the workflow templates wire Task B
    with a single ``TaskUpdate(B, owner=..., addBlockedBy=[A])``, and the
    co-occurrence is what marks the write TERMINAL for the ordering gate —
    both halves of the wiring settled in one place. ``TaskCreate(B)`` leaves
    owner empty (it is wired by a later update), and an addBlockedBy-ONLY
    write says the owner is still unsettled. That the composite is the
    ordering gate's signal does NOT make it the population's: live practice
    splits the write, which is why the dispatch_site membership leg is the
    broader ``is_owner_bearing_write`` above.

    Owners are BARE names (``backend-coder``), never ``pact-``-prefixed —
    ``pact-*`` is the team-config agentType. This function deliberately does no
    name-shape test at all; ``is_pact_specialist_owner`` owns that resolution.

    Pure function: no FS, no I/O, never raises. Non-dict input → False.
    Composes ``is_owner_bearing_write`` so the owner half of the test is
    shared by construction with the population's broader leg.
    """
    if not is_owner_bearing_write(tool_input):
        return False
    add_blocked_by = tool_input.get("addBlockedBy")
    return isinstance(add_blocked_by, list) and bool(add_blocked_by)


def variety_stamp_as_of_write(tool_input: object, task: object) -> dict:
    """The variety stamp the dispatched Task-B WILL CARRY once this write lands.

    A PreToolUse consumer is making a PREDICTION, so this models the PLATFORM'S
    WRITE, not a convenient view of the two sources. ``TaskUpdate`` merges
    metadata at the TOP-LEVEL KEY: naming ``variety`` REPLACES the whole nested
    dict, and every disk key the write does not name is DROPPED. So the
    post-write stamp is the incoming one when the write names ``variety`` at
    all, and the disk one otherwise. There is no merge, because the platform
    does not perform one.

    WHY THIS IS NOT A UNION, since the union is the intuitive shape and was the
    shipped one: ``{**disk_variety, **incoming_variety}`` describes a state that
    exists NOWHERE — not on disk, not in the write, and not after it. Resolving
    a total from it lets a caller re-stamping ONE dimension pass a gate while
    the write leaves the task holding only that dimension, i.e. UN-STAMPED. The
    union does not merely mis-measure; it blesses the exact write it exists to
    catch. It is wrong for BOTH consumers below, for this one reason, which is
    why the correction belongs here rather than at either call site.

    A WRITE THAT DELETES IS STILL A WRITE: ``metadata={"variety": None}`` is the
    platform's delete-the-key form. It NAMES ``variety``, so the post-write
    value is "absent" and this returns ``{}`` — the un-stamped answer, which is
    the truthful one.

    SHARED BY THE GATE AND THE EMIT ON PURPOSE. ``handoff_ordering_gate``
    ENFORCES against this value and ``task_lifecycle_gate``'s ``dispatch_site``
    emit RECORDS it. They must answer "what will this dispatch's stamp be?"
    identically or the calibration record carries samples for dispatches the
    gate refused — and a sample whose value never existed on disk.

    ALWAYS RETURNS A DICT, never None, and that is load-bearing rather than
    defensive: ``resolve_variety_total`` reaches its ``metadata["variety_score"]``
    candidate only when handed a dict, so returning None here would silently
    un-reach the non-canonical sibling spelling for every caller.

    UNFILTERED BY DESIGN — every key of the post-write stamp, NOT the canonical
    ``DISPATCH_VARIETY_KEYS`` projection. A caller wanting the journal
    projection applies it itself; a caller resolving a TOTAL must not, because
    ``resolve_variety_total`` accepts non-canonical candidates (``score``) that
    the projection discards.

    Pure: no FS, no I/O, never raises, and READ-ONLY on every input — builds one
    new dict and never mutates ``task``, ``tool_input`` or either metadata
    mapping. Either argument may be any type; a non-dict yields ``{}``.
    """
    def _variety_of(container: object) -> object:
        if not isinstance(container, dict):
            return None
        metadata = container.get("metadata")
        return metadata.get("variety") if isinstance(metadata, dict) else None

    incoming_metadata = (
        tool_input.get("metadata") if isinstance(tool_input, dict) else None
    )
    if isinstance(incoming_metadata, dict) and "variety" in incoming_metadata:
        post_write = incoming_metadata.get("variety")
    else:
        post_write = _variety_of(task)
    return dict(post_write) if isinstance(post_write, dict) else {}


# ─── task-assigned check ───────────────────────────────────────────────────

def has_task_assigned(team_name: str, name: str) -> bool:
    """True iff at least one task in ``team_name``'s task store has
    ``owner==name`` AND ``status in {"pending", "in_progress"}``.

    Delegates path construction and per-file reading to
    ``shared.task_utils.iter_team_task_jsons``, the single source of truth
    for per-team task iteration. That helper enforces the canonical
    ``~/.claude/tasks/{team_name}/*.json`` layout and applies path-traversal
    + symlink-escape defenses; centralizing the path here prevents the
    layout duplication that previously caused this gate to read the wrong
    directory. TaskList is a harness tool unavailable in subprocess
    context, so direct FS read via the helper is the only viable channel.
    """
    if not isinstance(team_name, str) or not team_name:
        return False
    if not isinstance(name, str) or not name:
        return False
    for data in iter_team_task_jsons(team_name):
        if data.get("owner") != name:
            continue
        if data.get("status") in ("pending", "in_progress"):
            return True
    return False
