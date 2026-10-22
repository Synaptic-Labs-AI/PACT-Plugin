#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/handoff_ordering_gate.py
Summary: PreToolUse hook (matcher="TaskUpdate") with TWO independent branches:
         (1) #956 completion-ordering nudge — WARNS the lead when a
         TaskUpdate(status="completed") lands on a HANDOFF-expecting task whose
         metadata.handoff is not yet present on disk. Advisory only; NEVER
         denies.
         (2) #865 dispatch-variety gate — fires when a terminal dispatch-wiring
         TaskUpdate (owner resolves to a pact-specialist agentType AND
         addBlockedBy in the SAME tool_input) links
         a Task B that carries no resolvable metadata.variety. DENIES by
         default; PACT_DISPATCH_VARIETY_MODE opts a consumer DOWN to
         warn/shadow. The deny path is the file's ONLY fail-CLOSED
         exception — every other path fails OPEN.
Used by: hooks.json PreToolUse hook (matcher="TaskUpdate")

This is the NUDGE half of the #956 fix (defense-in-depth). The load-bearing
half is the write-time BACKSTOP in task_lifecycle_gate.py's
`TaskUpdate && status != "completed"` block, which GUARANTEES the agent_handoff
re-emits when handoff is set later. This gate only surfaces an actionable
advisory so the lead can do handoff-then-complete in the clean order; it does
NOT block — a completing TaskUpdate always proceeds.

WHY THE #956 BRANCH WARNS AND NEVER DENIES (architect D2), and this reasoning
does NOT carry to the dispatch-variety branch: actor attribution is unreliable
on PreToolUse stdin (no agentId; CLAUDE.md "SendMessage is unhookable"
corollary), so a deny on a misjudged completion would strand it → livelock on
the completion-authority path, which is worse than the data-loss bug. The
backstop already recovers prevention's full value. The dispatch-variety branch
is not exposed to that: it keys on a STRUCTURAL shape and a task read rather
than on who is acting, and a refused wiring write is re-issuable by the same
caller in the same turn — nothing is stranded.

MODULE-LOAD FAILURE FAILS OPEN FOR BOTH BRANCHES, deny default notwithstanding:
a crashed PreToolUse hook (exit 1) is treated as non-blocking by the platform,
so on load failure we suppress + exit 0 rather than denying like the
fail-CLOSED gates (bootstrap_gate / pin_*_gate). A gate that cannot load cannot
have decided anything, so it must not be what refuses a write.

WHY PreToolUse (not PostToolUse): the choice is about advisory TIMING, not
deny power — this gate never denies on EITHER event. PreToolUse surfaces the
nudge in the SAME turn, BEFORE the completion lands, so the lead can choose
handoff-then-complete in the clean order while the decision is still live. A
PostToolUse advisory would arrive after the completion already applied — too
late to reorder. (The backstop, which DOES need the after-state, lives on the
PostToolUse lifecycle gate; this nudge wants the before-state.)

DUAL-MODE: lead-frame-only. The advisory is for the lead performing the
completion; key on pact_context.is_lead (reads agent_type — the only tmux-safe
discriminator; agent_id/team_name are absent on tmux frames). Emit nothing in a
teammate frame.

Input: JSON from stdin with tool_name, tool_input, agent_type, etc.
Output: JSON with hookSpecificOutput.additionalContext (advisory case) or
        {"suppressOutput": true} (allow / passthrough). ALWAYS exit 0.
"""

from __future__ import annotations

# ─── stdlib first (used on the input-side fail-open BEFORE wrapped imports) ─
import json
import sys

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# ─── #865 dispatch-variety enforcement mode (env-knob) ─────────────────────
# Models dispatch_gate.py's PACT_DISPATCH_INLINE_MISSION_MODE: read once at
# import. The SHIPPED default is "deny" — this gate ENFORCES, and a consumer
# opts DOWN to "warn" or "shadow" rather than opting up to enforcement.
#   deny   → permissionDecision:"deny" + exit 2 (the ONLY fail-CLOSED path in
#            this file), the shipped default.
#   warn   → additionalContext advisory (the existing WARN mechanism) + exit 0
#   shadow → journal-only calibration; no additionalContext, no deny.
#
# WHAT ARMING RESTS ON, because "it denies by default" is the claim most worth
# checking before touching anything here:
#   (a) The deny is HONORED. The platform's PreToolUse deny branch returns
#       before tool.call() with no tool_name carve-out, so a TaskUpdate-matcher
#       deny IS honored — source-proven, and observed live from the lead frame
#       on 2026-07-28: a dispatch-wiring TaskUpdate carrying no resolvable
#       variety was refused by the platform with this gate's own message, and
#       the refusal was ATOMIC (owner, blockers and metadata all unapplied).
#   (b) The gate does not block a faithful command. The same 2026-07-28 session
#       reproduced an OVER-BLOCK on the first attempt: a wiring write that
#       carried a complete variety stamp IN THE WRITE was refused, because the
#       predicate read the stamp from disk only. That is fixed below (the
#       incoming tool_input is overlaid on the disk read), and it was the
#       blocker for arming — not the honor question.
#   (c) Producers comply. All 14 gate-reachable Task-B wiring sites across
#       commands/ stamp variety at TaskCreate, so the stamp is on disk before
#       the wiring write lands.
#
# Resolution is delegated to the shared PACT_* resolver
# (shared.pact_config.get_enum): it applies the same .strip().lower()
# normalization BEFORE the membership check ("DENY" / " deny " / "Deny" → deny)
# and owns the allowed set in its registry (SSOT). NOTE that UNSET and
# MISSPELLED do NOT land in the same place: an unset var takes the enforcing
# default "deny", while an unrecognized value resolves to the row's declared
# invalid_fallback of "warn". Absence is consent to the shipped posture; a
# value the resolver cannot parse is not a request to enforce, and the only
# consumers reaching that branch are ones trying to opt DOWN. The resolver's
# stderr line names the value it actually resolved to.
#
# The fail-open guard below is DELIBERATELY "warn" and must NOT follow the
# registry default: it fires when the resolver itself is unavailable, and a
# gate that cannot read its own configuration must not be the thing that
# refuses a write. Enforcement is a decision, not a fallback.
# (handoff_ordering_gate is NOT a seam-dependent hook, so this import does NOT
# get a _SEAM_HOOK_HELPER_CLOSURE entry — unlike dispatch_gate / session_init.)
try:
    import shared.pact_config as pact_config
    DISPATCH_VARIETY_MODE = pact_config.get_enum("PACT_DISPATCH_VARIETY_MODE")
except BaseException:  # noqa: BLE001 — fail-OPEN: resolver unavailable → safe default
    DISPATCH_VARIETY_MODE = "warn"

# Cap on the stdin read. Real PreToolUse TaskUpdate frames carry a tool_input
# (taskId + small metadata) and stay well under this; an over-cap frame
# truncates mid-JSON → JSONDecodeError → input-side fail-open. Bounds memory
# only; does not reject sub-cap input. Mirrors the gate twins' 8 MB cap.
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


# ─── fail-OPEN wrapper on cross-package imports ────────────────────────────
# A WARN gate must NEVER deny. If an import below raises, we suppress + exit 0
# (fail-open) rather than emitting a deny — unlike the fail-CLOSED deny gates.
# A crashed hook (exit 1) is ALSO non-blocking on PreToolUse, so even an
# un-caught raise degrades to fail-open; the explicit catch keeps the exit code
# clean (0) and the output well-formed.
try:
    import shared.pact_context as pact_context
    from shared.dispatch_helpers import (
        is_pact_specialist_owner,
        is_owner_wiring_shape,
        variety_stamp_as_of_write,
    )
    from shared.intentional_wait import is_self_complete_exempt
    from shared.task_utils import is_teachback_subject, read_task_json, resolve_task_dir
    from shared.teachback_schema import resolve_variety_total
    _IMPORTS_OK = True
except BaseException:  # noqa: BLE001 — fail-OPEN catch-all (warn gate never denies)
    _IMPORTS_OK = False


def _evaluate(input_data: dict) -> str | None:
    """Return an actionable advisory string when the completing TaskUpdate is
    the #956 ordering mistake, else None.

    The ordering mistake = a lead-frame TaskUpdate(status="completed") on a
    HANDOFF-expecting task whose metadata.handoff is absent BOTH in this update
    (incoming) AND on disk (existing). Pure-ish read; never denies.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name != "TaskUpdate":
        return None  # matcher already scopes this, but be defensive

    # DUAL-MODE: lead frame only. is_lead reads agent_type (structural,
    # mode-agnostic). A teammate frame emits nothing.
    if not pact_context.is_lead(input_data):
        return None

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    if tool_input.get("status") != "completed":
        return None  # only completion transitions

    # Is handoff being set in THIS same TaskUpdate? Then it is a bundled
    # handoff+complete — no race, no warn.
    incoming_metadata = tool_input.get("metadata")
    incoming_handoff = (
        incoming_metadata.get("handoff")
        if isinstance(incoming_metadata, dict)
        else None
    )
    if isinstance(incoming_handoff, dict) and incoming_handoff:
        return None

    # Read CURRENT on-disk task state (PreToolUse: the update has NOT applied
    # yet). team_name resolved via pact_context (init seeds the context path).
    task_id = tool_input.get("taskId", "") or ""
    if not task_id:
        return None
    try:
        pact_context.init(input_data)
    except Exception:
        pass
    # #1020 migration: resolver seam (live store's dir name) replaces the raw
    # persisted read — on config-less substrates the raw name addressed the
    # absent tasks/session-<id8>/ store while the tasks lived under the full
    # session UUID. resolve_task_dir never raises; "" on no-store/ambiguity.
    resolved_task_dir = resolve_task_dir()
    team_name = resolved_task_dir.name if resolved_task_dir else ""
    if not team_name:
        return None  # no team context → cannot resolve the task → bypass

    task = read_task_json(task_id, team_name)
    if not isinstance(task, dict) or not task:
        return None  # no task data → bypass (fail-open)

    # Handoff already on disk? Then completing is fine — no race.
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    existing_handoff = metadata.get("handoff")
    if isinstance(existing_handoff, dict) and existing_handoff:
        return None

    # HANDOFF-expecting predicate (the SSOT-reuse composition):
    #   exempt(task)            = is_self_complete_exempt(task, team_name)   # secretary + signal-task
    #                             OR is_teachback_subject(subject)            # Task-A
    #   handoff_expecting(task) = owner is a non-empty string (teammate, bare
    #                             name) AND NOT exempt(task)
    owner = task.get("owner") or ""
    if not isinstance(owner, str) or not owner.strip():
        return None  # no owner → not a teammate work task
    subject = task.get("subject") or ""
    # The is_self_complete_exempt arm suppresses the warn for the agent types in
    # SELF_COMPLETE_EXEMPT_AGENT_TYPES (currently the secretary) + signal tasks.
    # If that exempt set GROWS, re-audit this suppression: a newly-exempt type
    # that DOES carry a HANDOFF would silently lose the nudge here. See the
    # is_self_complete_exempt docstring (shared/intentional_wait.py) for the
    # canonical exempt-surface definition.
    if is_self_complete_exempt(task, team_name) or is_teachback_subject(subject):
        return None  # exempt → no handoff expected, no warn

    # HANDOFF-expecting + completing + handoff absent (neither incoming nor on
    # disk) = the #956 ordering mistake. WARN with an ACTIONABLE message.
    return (
        f"PACT handoff_ordering_gate: Task {task_id} ({subject!r}, owner {owner!r}) "
        "is being completed but has no metadata.handoff yet. The agent_handoff "
        "journal event keys on handoff presence at completion time — completing "
        "now risks losing it. EITHER (a) wait for / write the teammate's "
        "metadata.handoff BEFORE marking completed, OR (b) confirm this task is "
        "genuinely handoff-exempt. A write-time backstop will re-emit if handoff "
        "is set later, but the cleanest path is handoff-then-complete."
    )


def _variety_stamp_attempted(*metadatas: object) -> bool:
    """True iff any given metadata mapping carries something the consumer
    plainly INTENDED as a variety stamp — a ``variety`` key of any shape, or
    the non-canonical ``variety_score`` sibling.

    This separates NOTHING was written (remedy: write the block) from
    something WAS written that does not resolve (remedy: look at the values,
    not the field list) — two of the situations ``resolve_variety_total``
    collapses into a single None. Deliberately shape-BLIND: a ``variety``
    that is a string, a list, or an empty dict is still an attempt, and
    telling that consumer to "add the block" points them away from the
    actual defect.

    It decides only WHICH SENTENCE the deny carries. Both situations still
    deny; there is no carve-out here and adding one would let the malformed
    case through, which is the case most likely to be a silent wrong number
    downstream. Pure; never raises.

    NOT THE WHOLE SPLIT — see ``_variety_write_drops_disk_keys``, which is
    consulted FIRST and peels off the case where the values are fine and the
    WRITE is what broke the stamp. This predicate is shape-blind, so it
    cannot tell that case apart on its own.
    """
    for metadata in metadatas:
        if isinstance(metadata, dict) and (
            "variety" in metadata or "variety_score" in metadata
        ):
            return True
    return False


def _is_bare_null_delete(
    disk_metadata: object, incoming_metadata: object,
) -> bool:
    """True iff this write DELETES ``metadata.variety`` and the disk carried
    no stamp attempt of its own — nothing was supplied by either side.

    WHY THIS EXISTS SEPARATELY FROM THE SHAPE-BLINDNESS. A ``variety`` that
    arrives as a string, a list or an empty dict SUPPLIES A BAD VALUE, and for
    those the second sentence is true: something is there, re-read it. A
    ``None`` SUPPLIES NO VALUE — it is the platform's remove-the-key form — so
    telling that consumer to re-read their values names something that does
    not exist. The remedy is the first sentence: stamp the block.

    That is the whole distinction, and it is the reason
    ``_variety_stamp_attempted`` is left exactly as it is rather than taught
    about nulls. Its shape-blindness is correct for every shape that carries a
    value; a null is the one shape that carries none.

    ASKS THE DISK QUESTION BY CALLING ``_variety_stamp_attempted`` rather than
    re-deriving it. There is one definition of "attempted" and this is not a
    second one — if that predicate's notion ever widens, this follows it.

    NARROWER THAN IT LOOKS, because of where it is consulted: the
    partial-replace branch runs FIRST and has already claimed every null that
    lands on a real disk stamp. A null reaching this predicate therefore has a
    disk ``variety`` that is absent, non-dict, or empty. The disk's own
    attempt signal stays decisive, so a null over ``{"variety": {}}`` or over
    a ``variety_score`` sibling keeps the second sentence.

    Pure; never raises.
    """
    if not isinstance(incoming_metadata, dict):
        return False
    if "variety" not in incoming_metadata:
        return False
    if incoming_metadata["variety"] is not None:
        return False
    return not _variety_stamp_attempted(disk_metadata)


def _variety_write_drops_disk_keys(
    disk_metadata: object, incoming_metadata: object,
) -> bool:
    """True iff this write NAMES ``metadata.variety`` and the on-disk stamp
    holds at least one key the write does not carry forward.

    THE THIRD DIAGNOSIS, and it exists because the other two both misread
    this case. ``TaskUpdate`` merges metadata at the TOP-LEVEL key, so naming
    ``variety`` REPLACES the whole nested dict: every disk key the write
    omits is dropped and the task lands holding only what was sent. The
    consumer's VALUES are fine here, so the "re-read the values" sentence
    sends them hunting for a defect that is not there; and they plainly did
    stamp, so the "add the block" sentence is wrong in the other direction.
    The defect is the SHAPE OF THE WRITE, and the remedy is to send the whole
    block.

    PARTIALNESS, NOT CONTAINMENT, and the distinction is the whole point of
    the predicate. A write carrying keys the disk stamp LACKS still replaces
    it, so this asks only whether the write DROPS something. Testing that
    the incoming keys sit inside the disk keys would exclude the disjoint
    case — which is the one that motivated the correction, so a containment
    reading would report clean over a population missing the defect.

    A DELETE IS THE SAME DEFECT AT FULL STRENGTH. ``{"variety": None}`` is
    the platform's remove-the-key form: it NAMES ``variety`` and carries
    nothing forward, so it drops every key the disk held. The values are not
    wrong — there are none — so the values sentence is even less use here
    than for a partial block.

    A MALFORMED VALUE IS NOT THIS CASE, and the split is deliberate: a
    ``variety`` that arrives as a string or a list also lands the task
    un-stamped, but there the consumer really did send a bad VALUE and the
    second sentence is the true one. Only ``None`` reads as "carried nothing
    forward" rather than "sent something wrong".

    GUARDED ON THE DISK SIDE: with no on-disk stamp to lose, nothing was
    dropped and this is not the diagnosis, whatever the write looks like.
    An empty on-disk ``variety`` counts as nothing to lose.

    A SAME-KEY OVERWRITE DROPS NOTHING and is likewise NOT this case:
    a disk ``{"total": 12}`` overwritten by ``{"total": "x"}`` leaves the
    field list intact, so there too the values are the defect. That pair is
    what separates the two sentences.

    Decides only WHICH SENTENCE the deny carries — every case reaching it
    denies either way. Pure; never raises.
    """
    if not isinstance(incoming_metadata, dict):
        return False
    if "variety" not in incoming_metadata:
        return False
    disk_variety = (
        disk_metadata.get("variety") if isinstance(disk_metadata, dict) else None
    )
    if not isinstance(disk_variety, dict) or not disk_variety:
        return False
    incoming_variety = incoming_metadata["variety"]
    if incoming_variety is None:
        return True
    if not isinstance(incoming_variety, dict):
        return False
    return bool(set(disk_variety) - set(incoming_variety))


def _evaluate_dispatch_variety(input_data: dict) -> str | None:
    """#865: return an actionable advisory string when a terminal
    dispatch-wiring TaskUpdate links a Task B that carries no resolvable
    metadata.variety, else None. The caller decides warn-vs-deny-vs-shadow
    from DISPATCH_VARIETY_MODE; this function only detects the gap.

    This is a NEW branch, parallel to and independent of the #956
    completion-ordering _evaluate — neither calls the other.

    COMPOSITE-SIGNATURE TRIGGER (the FIRST-OBSERVABLE-WRITE / no-misfire
    invariant): fire ONLY on the terminal dispatch-wiring write — a single
    TaskUpdate whose tool_input carries BOTH:
      - an owner that resolves (via team config) to a pact-specialist
        agentType — owners are BARE names, so this is a team-config
        resolution, NOT an owner.startswith("pact-") prefix check, AND
      - addBlockedBy present and non-empty (the teachback-gate link),
    in the SAME tool_input. This composite co-occurrence is uniquely the
    dispatch-wiring shape (orchestrate/comPACT/plan-mode/rePACT all wire B
    via `TaskUpdate(B, owner=..., addBlockedBy=[A])`). No fire at
    TaskCreate(B) (owner empty there — wired by this later TaskUpdate) or on
    a partial-wiring TaskUpdate (owner-only OR addBlockedBy-only). All other
    addBlockedBy uses across the templates (phase/imPACT blocker blocking)
    are addBlockedBy-ONLY with no owner in the same call → already excluded.

    STRUCTURAL DECISION (not actor-based): the gate READS the linked Task B's
    metadata.variety from disk OVERLAID WITH THE INCOMING WRITE, and fires ONLY
    when there is no resolvable total (absent / non-dict / untotaled). Firing
    on the composite signature alone would warn on every dispatch including
    correctly-stamped ones; the read is what makes the decision
    detection-precise (and the deny safe). The overlay is what keeps a caller
    that wires and stamps in ONE call from being refused — see the merge site
    below. The "present-but-malformed-rationale" case stays a PostToolUse
    advisory in task_lifecycle_gate R4 (the surgical split) — this gate keys
    solely on resolve_variety_total being None. That single trigger reports as
    THREE MESSAGES (no stamp at all / a stamp that does not resolve / a
    partial write that replaced a fuller stamp) because the remedies are
    mutually wrong; the TRIGGER is not split, only the sentence.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name != "TaskUpdate":
        return None  # matcher already scopes this, but be defensive


    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    # COMPOSITE signature — a pact-specialist owner AND addBlockedBy non-empty
    # in the SAME tool_input. Either half alone is a non-terminal/partial write.
    # Cheap in-memory guards FIRST (the shape predicate, then taskId); the
    # owner→agentType resolution is a disk read, deferred until after the
    # team_name resolve below (cost-order).
    #
    # The SHAPE half is delegated to shared.dispatch_helpers. This gate and
    # the dispatch-coverage denominator deliberately key on DIFFERENT shape
    # legs — the denominator on the broader owner-BEARING write (live
    # practice wires owners in split writes), this gate on the composite —
    # and the composite RECOMPOSES that owner-bearing predicate plus the
    # blockedBy leg, so the two consumers share the owner leg by
    # construction: they cannot drift on what "owner-bearing" means, which
    # is the parallel-path hazard the delegation exists to close. Only the
    # shape is shared: the owner→specialist resolution and the exemption
    # predicate stay here, because the exemption question this gate asks
    # ("may this owner self-complete?") is NOT the question the denominator
    # asks. See is_owner_wiring_shape's docstring for the full recognition
    # and why the legs are kept separate. NOTE the shared helper
    # says "shape" rather than this file's local "terminal", because that
    # term is defined HERE (terminal = both halves present, vs a partial
    # one-half write) and does not travel to a module with other consumers.
    if not is_owner_wiring_shape(tool_input):
        return None  # partial wiring (owner-only or blockers-only) → not terminal
    # Guaranteed a non-empty, non-whitespace str by the shape predicate above.
    owner = tool_input.get("owner", "")

    task_id = tool_input.get("taskId", "") or ""
    if not task_id:
        return None
    try:
        pact_context.init(input_data)
    except Exception:
        pass
    # #1020 migration: same resolver-seam replacement as the ordering check —
    # raw persisted name -> resolve_task_dir() ("" on no-store/ambiguity).
    resolved_task_dir = resolve_task_dir()
    team_name = resolved_task_dir.name if resolved_task_dir else ""
    if not team_name:
        return None  # no team context → cannot resolve owner/Task B → bypass

    # ORDER IS LOAD-BEARING: this sits AFTER pact_context.init(), not before.
    # is_canonical_journal_frame's topology leg resolves the team through
    # get_team_name(), which reads the context init() populates. In a fresh
    # hook subprocess that context is unset until init runs, so the leg
    # resolves to "" and returns False for EVERY non-lead frame — the
    # predicate would silently degrade to is_lead and this widening would be
    # a no-op in production while passing any test whose fixture pre-seeds a
    # context. Placing it here also keeps the cheap in-memory guards above it,
    # so the cost order is unchanged: nothing new is read for a frame that was
    # going to be rejected on shape anyway.
    # FRAME GATE — is_canonical_journal_frame, NOT is_lead, matching the
    # dispatch_site emit this gate is supposed to enforce over. The two were
    # split: the emit RECORDED a site on any canonical frame while enforcement
    # only reached the lead's, so an in-process teammate's wiring write was
    # counted and never enforced. Enforcing over a narrower population than the
    # one being recorded is the divergence, and the wider predicate is the one
    # the design fixed on.
    #
    # WHAT THIS ADMITS, stated because "wider" is not self-explanatory:
    # is_canonical_journal_frame returns True on is_lead, then FALLS THROUGH to
    # a topology leg comparing session_id against the team's leadSessionId. An
    # IN-PROCESS teammate shares the lead's session_id and is now admitted; a
    # TMUX teammate carries a distinct session_id and is still not. So this
    # widens to exactly one new class of frame, not to teammates generally.
    #
    # THE WIDENING CAN ONLY ADD DENIES, so it is the direction that needs a
    # remedy to exist before it ships. It does: every dispatch-wiring site in
    # the shipped templates is lead-frame and already stamped, and the
    # specialist autonomy path is a conceptual mini-cycle with no task
    # mechanics at all (pact-s1-autonomy.md defines no TaskCreate/TaskUpdate),
    # so no shipped instruction directs a teammate to wire a dispatch. The one
    # sentence that read as if it did is corrected in rePACT.md alongside this.
    if not pact_context.is_canonical_journal_frame(input_data):
        return None

    # CORRECTED PREDICATE (#865 cycle-1): identify a pact-specialist teammate by
    # resolving the BARE owner → team-member → agentType (the same resolution
    # the carve-out helpers use), NOT by an owner.startswith("pact-") prefix —
    # real owners are bare names, so the old prefix check was always False (the
    # gate was dead-on-arrival). is_pact_specialist_owner fail-CLOSES to False on
    # any unresolvable path → this gate fail-OPENS (return None), never strands.
    # SOLO_EXEMPT agents (general-purpose/Explore/Plan) have non-pact agentTypes
    # → excluded here naturally; the secretary (pact-secretary) PASSES this check
    # and is suppressed by the is_self_complete_exempt carve-out below.
    if not is_pact_specialist_owner(owner, team_name):
        return None  # owner does not resolve to a pact specialist → not a dispatch

    task = read_task_json(task_id, team_name)
    if not isinstance(task, dict) or not task:
        return None  # no task data → bypass (fail-open)

    # CARVE-OUTS (preserve R4's silence guarantees verbatim; the helpers are
    # already imported). The pact-specialist resolution above admits the
    # secretary (pact-secretary IS a registered specialist), so the
    # is_self_complete_exempt carve-out is LOAD-BEARING here — it suppresses
    # the secretary + signal tasks. is_teachback_subject suppresses the Task-A
    # teachback gate by subject.
    #
    # THE OWNER AS OF THIS WRITE, for the same reason the STAMP is read as of
    # this write below. This gate ADMITS on ``tool_input["owner"]`` (the
    # is_pact_specialist_owner check above) but the exemption predicate resolves
    # ``task["owner"]`` from DISK — and at the terminal wiring write the disk
    # owner is still empty, because THIS write is what sets it. Resolving the
    # admit from one source and the exemption from the other means the carve-out
    # cannot see the owner that admitted the write: ``_is_exempt_agent_type``
    # returns False on an empty owner, so every exempt owner fell through to
    # enforcement. That is an OVER-BLOCK, and it shipped on a deny default.
    #
    # The overlay is READ-ONLY on ``task`` — a new dict, so nothing downstream
    # sees a mutated record — and it is deliberately NOT a change to
    # ``is_self_complete_exempt``. That predicate is shared with the
    # task_lifecycle_gate self-completion advisory and audit tooling, and its
    # docstring carries a trust-boundary argument about ``owner`` being
    # teammate-writable; redefining "the owner" inside it would edit that
    # argument for consumers this change never examined. The gate owns which
    # owner it is asking about; the predicate owns what exemption means.
    subject = task.get("subject") or ""
    task_as_of_this_write = {**task, "owner": owner}
    if (
        is_self_complete_exempt(task_as_of_this_write, team_name)
        or is_teachback_subject(subject)
    ):
        return None

    # STRUCTURAL READ: does the linked Task B carry a resolvable variety total?
    # resolve_variety_total is the shared SSOT (also used by the read-time band
    # resolver and write-time validator). None ⇒ absent / non-dict / untotaled
    # ⇒ the missing-stamp gap this gate enforces. A resolvable total ⇒ silent
    # (a present-but-malformed-rationale stamp is R4's PostToolUse concern).
    #
    # THE STAMP AS OF THIS WRITE — disk OVERLAID with the incoming tool_input,
    # not disk alone. A disk-only read REFUSES
    #   TaskUpdate(B, owner=..., addBlockedBy=[A], metadata={"variety": {...}})
    # — one faithful command that wires and stamps together — because at
    # PreToolUse the stamp is in the write and not yet on disk. Blocking a
    # faithful single command is the cardinal failure for a control that can
    # deny, so this overlay is what makes deny mode shippable at all. The merge
    # is SHARED with the dispatch_site emit (see variety_stamp_as_of_write) so the
    # stamp this gate enforces and the stamp that emit records cannot drift.
    disk_metadata = task.get("metadata")
    incoming_metadata = tool_input.get("metadata")
    variety = variety_stamp_as_of_write(tool_input, task)
    # The `metadata` argument feeds ONLY resolve_variety_total's non-canonical
    # `metadata["variety_score"]` sibling candidate, which needs the same
    # overlay for the same reason — otherwise the atomic wire+stamp stays
    # blocked for anyone using that spelling. Merged at the METADATA level
    # here, which is safe precisely because the value read from it is a scalar
    # rather than a dict a partial write could truncate; `variety` is re-seated
    # from the key-level merge so the two arguments stay consistent even if the
    # resolver ever starts reading metadata["variety"].
    metadata: dict = {}
    for source in (disk_metadata, incoming_metadata):
        if isinstance(source, dict):
            metadata.update(source)
    metadata["variety"] = variety
    if resolve_variety_total(variety, metadata) is not None:
        return None  # stamp resolves → not a missing-stamp dispatch

    # THREE STATES BEHIND ONE None. resolve_variety_total collapses them, and
    # their remedies are mutually wrong: "add the block" versus "the block you
    # added does not resolve" versus "the block you sent is fine but it
    # REPLACED a fuller one". Telling a consumer who already stamped to stamp
    # again sends them in circles looking for a missing field; telling a
    # consumer whose values are correct to re-read those values sends them
    # after a defect that is not there. NOT A CARVE-OUT — all three still
    # deny; only the sentence differs.
    #
    # PARTIAL-REPLACE IS TESTED FIRST because it is a SUBSET of "attempted":
    # a partial write names `variety`, so the shape-blind predicate below
    # would claim it and hand out the values sentence. Order is the only
    # thing keeping the narrower diagnosis reachable.
    if _variety_write_drops_disk_keys(disk_metadata, incoming_metadata):
        diagnosis = (
            "REPLACES the stamp already on disk without carrying it whole. "
            "TaskUpdate merges metadata at the TOP-LEVEL key, so naming "
            "variety overwrites the whole nested dict and every key this "
            "write does not name is DROPPED — and naming it with a null "
            "removes the stamp outright. Post-write the task holds only what "
            "this write sent, which does not resolve to a total. The values "
            "sent are fine; the SHAPE of the write is the defect. Re-send the "
            "ENTIRE block (novelty/scope/uncertainty/risk + total 4-16) "
            "rather than only the keys being changed"
        )
    # THE NULL CARVE-OUT rides as a conjunct here rather than as a change to
    # the shape-blind predicate, which stays exactly as it is. A write that
    # DELETES variety over a disk that never attempted one supplied no value
    # to re-read, so the values sentence names nothing; it falls through to
    # "stamp the block", which is the only remedy that is true of it.
    elif (
        _variety_stamp_attempted(disk_metadata, incoming_metadata)
        and not _is_bare_null_delete(disk_metadata, incoming_metadata)
    ):
        diagnosis = (
            "carries a metadata.variety stamp that does NOT resolve to a "
            "total. resolve_variety_total accepts, in order: variety.total, "
            "variety.score, metadata.variety_score, or all four of "
            "novelty/scope/uncertainty/risk — each an int (not a bool, not a "
            "numeric string) in range, 4-16 for a total and 1-4 for a "
            "dimension. The stamp is present, so re-read the VALUES rather "
            "than adding the block again"
        )
    else:
        diagnosis = (
            "carries no metadata.variety stamp at all. Stamp the D11 "
            "4-rationale block (novelty/scope/uncertainty/risk + total 4-16) "
            "on this Task B — either at TaskCreate or in this same wiring "
            "write — mirroring the block in orchestrate.md / comPACT.md / "
            "peer-review.md / plan-mode.md / rePACT.md"
        )
    return (
        f"PACT dispatch-variety gate: Task {task_id} ({subject!r}) is being "
        f"wired into a teachback-gated dispatch (owner {owner!r}) and "
        f"{diagnosis}. Per-dispatch variety stamping is required so the hook "
        "can resolve the reasoning_reconstruction band and the "
        "concurrent-auditor trigger. To opt this enforcement down, set "
        "PACT_DISPATCH_VARIETY_MODE=warn (advisory) or =shadow (journal "
        "only); it defaults to deny."
    )


def _emit_warn(advisory: str) -> None:
    """WARN output path: additionalContext advisory + exit 0 (never denies).
    Shared by the #956 nudge and the dispatch-variety warn mode."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": advisory,  # advisory — NOT permissionDecision
        }
    }))
    sys.exit(0)  # exit 0 — advisory, never deny / exit-2


def main() -> None:
    # Input-side fail-open: an unreadable / oversized / malformed stdin frame
    # suppresses + exits 0 (never blocks the TaskUpdate).
    try:
        input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not _IMPORTS_OK or not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # #865 dispatch-variety branch FIRST: it is the only branch that can DENY
    # (deny mode), and a denied wiring write should be blocked before the #956
    # completion nudge is even considered. Both branches fail-OPEN on any logic
    # error — a gate that bricks legitimate writes is worse than the gap it
    # guards. The deny path (deny mode + confirmed missing stamp) is the sole
    # deliberate fail-CLOSED exception.
    try:
        variety_gap = _evaluate_dispatch_variety(input_data)
    except Exception:
        variety_gap = None  # fail-OPEN on any logic error
    if variety_gap:
        if DISPATCH_VARIETY_MODE == "deny":
            # The ONLY fail-CLOSED path in this file, and the shipped default.
            # Honor is source-proven (the platform PreToolUse deny branch
            # returns before tool.call() with no tool_name carve-out) AND
            # observed: on 2026-07-28 this branch refused a real
            # dispatch-wiring TaskUpdate from the lead frame, atomically —
            # after the refusal the task showed owner unset, blockedBy empty
            # and metadata unwritten, so a caller must re-issue the WHOLE
            # write, not just the missing stamp.
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": variety_gap,
                }
            }))
            sys.exit(2)
        if DISPATCH_VARIETY_MODE == "warn":
            _emit_warn(variety_gap)
        # shadow → fall through to suppress (journal-only telemetry is the
        # PostToolUse R4/journal surface; here shadow simply does not surface).

    # #956 completion-ordering nudge: WARN-only, never denies.
    try:
        advisory = _evaluate(input_data)
    except Exception:
        # WARN gate → fail-OPEN on any logic error. A warn gate that bricks
        # completions is worse than the bug it warns about. NEVER deny.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if advisory:
        _emit_warn(advisory)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
