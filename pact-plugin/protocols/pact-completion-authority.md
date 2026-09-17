## Completion Authority

> **Purpose**: Lead-only completion of teammate-owned tasks. Acceptance is a two-call atomic pair (status flip FIRST, then wake-signal `SendMessage`); rejection is dual-channel (metadata write FIRST, then wake-signal `SendMessage`).
>
> **Audience**: PACT team-lead (orchestrator). Teammate-side rules live in [pact-agent-teams §On Completion](../skills/pact-agent-teams/SKILL.md#on-completion--handoff-required) and [pact-agent-teams §On Rejection](../skills/pact-agent-teams/SKILL.md#on-rejection-wake-signal-receipt).

You — the team-lead — are the **only** actor who marks teammate-owned tasks `completed`. Teammates write HANDOFFs to `metadata.handoff`, idle on `intentional_wait{reason=awaiting_lead_completion}`, and wait for your acceptance. The `TaskUpdate(status="completed")` flip is the load-bearing approval action; the paired wake-signal `SendMessage` is the load-bearing wake.

`blockedBy` is pull-only at the platform level — the platform does NOT push a wake on blocker resolution; `blockedBy` is computed at `TaskList` query time. Idle teammates cannot self-wake to re-poll, so the wake-signal `SendMessage` is paired with each metadata or status write that resolves their wait.

### Acceptance — two-call atomic pair (BOTH required, `TaskUpdate` FIRST)

1. `TaskUpdate(taskId, status="completed")` — status flip; auto-unblocks any tasks with `blockedBy=[<id>]`
2. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Task #<id> accepted. Work complete.", summary="Task accepted")` — wakes the idle teammate so they can claim the next task

Both calls are **required**, `TaskUpdate` FIRST — the wake-signal `SendMessage` is the last call. If the `TaskUpdate` succeeds and the `SendMessage` errors, retry the send on the tool error; a teammate left gate-open with no wake is recovered only when something wakes them (the retried send, a peer message, your next dispatch) — their disk-first re-read then resolves the open gate. Skipping the `SendMessage` entirely strands the teammate idle on `awaiting_lead_completion` until something else (peer message, your next dispatch) wakes them; `blockedBy` resolution is invisible without the wake.

### Rejection — two-call atomic pair (BOTH required, `TaskUpdate` FIRST)

1. `TaskUpdate(taskId, metadata={"teachback_rejection": {...}})` (Task A) OR `TaskUpdate(taskId, metadata={"handoff_rejection": {...}})` (Task B) — payload `{reason, corrections, since, revision_number}`
2. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Rejected on Task #<id>. REJECTION-PAYLOAD-BEGIN reason: <one-line summary> corrections: [<correction 1>, <correction 2>, ...] since: <canonical_since() output> revision_number: <N> REJECTION-PAYLOAD-END The payload is a verbatim copy of metadata.{teachback,handoff}_rejection. Revise.", summary="Rejected; revise")` — wakes the teammate; the corrections they act on ride the message

Both calls are **required**, and the ordering matches Acceptance for the same reason: the wake-signal `SendMessage` is what un-idles the teammate, who cannot self-observe the rejection metadata on a pull-only wait. If the `TaskUpdate` succeeds and the `SendMessage` errors, retry the send on the tool error. Skipping the `SendMessage` leaves the teammate idle on stale `awaiting_lead_completion`, never seeing the corrections — symmetric failure to skipping wake on acceptance. The teammate's `intentional_wait` does not auto-clear when you write rejection metadata; only the wake-signal triggers their CLEAR-and-revise flow. **3+ rejection cycles** on the same task is an imPACT META-BLOCK signal.

**Teammate self-completion carve-outs (predicate-witnessed)** — narrow exemptions where the teammate marks `completed` themselves:

| Carve-out | Trigger | Rule |
|---|---|---|
| Signal-tasks | `metadata.completion_type == "signal"` AND `metadata.type ∈ {"blocker", "algedonic"}` | Blocker- and algedonic-signal tasks self-complete; the task IS the signal, no HANDOFF to judge. Auditor observation tasks carry `completion_type="signal"` with NO `metadata.type`, so this predicate does not witness them — they self-complete as documented practice in the Concurrent Audit Protocol, not as a predicate-witnessed exemption. Do NOT add `metadata.type` to an auditor dispatch to make it fit the predicate; the carve-out set is a policy surface, not a template detail. |
| Secretary session briefing + memory-save | Owner's team-config `agentType` ∈ `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (currently `{pact-secretary}`) | Secretary self-completes its session-briefing deliverable (`secretary: deliver session briefing`) as the final act of delivering the briefing, and its memory-save tasks; team-lead has no acceptance criteria for the secretary's own briefing or memory bookkeeping. Self-completion does not end the secretary's role (it stays alive as consultant + harvester). Resolved via team-config lookup on `member.agentType`, so the carve-out applies regardless of spawn name (`session-secretary`, etc.). |

The canonical predicate `is_self_complete_exempt(task, team_name)` in `shared/intentional_wait.py` witnesses ONLY these two surfaces. It is a pure function read by the PostToolUse advisory gate `task_lifecycle_gate.py` (advisory-only — it cannot DENY; it emits a `self_completion` advisory + a `completion_disputed` writeback when a non-exempt teammate self-completes) as well as by your own inspection of the task file and audit tooling. No hook BLOCKS on it; the exemption is not enforced (nothing forces an exempt teammate to self-complete — that remains instruction-level). Pass `team_name` (read from session context) to get accurate exemption signal for surface 1; surface 2 is independent of `team_name`.

**Related (dispatch surface)**: `member.agentType="pact-secretary"` also gets a dispatch carve-out — no TEACHBACK (single-task dispatch). Second agentType-keyed carve-out, parallel to `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (completion, above); two frozensets, two behavioral surfaces, fully decoupled. See `agents/pact-orchestrator.md` §11 + `commands/bootstrap.md`.

**Lead-driven force-completion (separate path, not predicate-witnessed)**:

| Path | Trigger | Rule |
|---|---|---|
| imPACT termination | `metadata.terminated == true` | You force-complete an unrecoverable agent's task via `TaskStop` + `TaskUpdate(status="completed", metadata={"terminated": true, "reason": "..."})`. See [imPACT.md](../commands/imPACT.md). The `terminated` marker is recognized directly by audit/inspection; `is_self_complete_exempt` does NOT cover this surface (the team-lead writes status=completed directly). |

**`TaskGet` metadata-blindness reminder**: `TaskGet` does NOT surface metadata. Read directly:

```
cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.handoff
```

Accept or reject on the payload carried by the teammate's notify `SendMessage` — its arrival is demonstrable; it is the wake. The raw metadata read is a DEFERRED audit: run it at your next turn boundary, before acting on that teammate's next submission. A disk copy that is missing or diverges from the message copy is a data-integrity finding — record it per the Read-Trigger Precondition's deferred-audit point and, when the disk copy is absent, repair it by re-writing the payload to metadata from the message copy while it is still in your context. It is never a basis for rejecting a submission you have already acted on.

### Crossed-Wake Idles: Discriminate by Timestamp Direction

Your wake-signal `SendMessage` races the teammate's turn-end idle notification: inbox
files are written asynchronously on delivery, so a teammate's idle notification can
reach you AFTER your directive send while the teammate has not yet seen the wake. This
happens at every wait-resolution seam — teachback acceptance, HANDOFF acceptance,
commit confirmation, rejection — and identically under in-process and tmux
teammateMode: the race is delivery-ordering, not mode-specific. The
teachback-acceptance seam named here is this delivery-ordering race — your wake
crossing the teammate's idle notification — not the disk-read stranding race the
TaskUpdate-first pair ordering removed. The idle
notification alone cannot distinguish "idle because my wake has not landed yet"
from "idle because the teammate stalled".

Discriminate by direction — the race crosses your directive in BOTH orders.
An idle notification from a teammate you have just directed (any wake,
confirm, or other send that resolves the teammate's wait) either predates
your directive send or postdates it, and the two cases get different
handling:

- **A notification that predates your directive send is a straggler.** The
  idle event fires at the end of the teammate's turn, so a tick generated
  before your directive was sent is a late delivery of prior-turn state.
  Take no action and proceed — it is not a stall signal at all.
- **On ambiguous timestamps, one durable read settles it.** Take the durable
  read of step 1 below (raw JSON — `TaskGet` is metadata-blind) and apply its
  predicate: a claim present means the teammate is proceeding on the
  directive; a claim absent falls through to the postdating procedure (one
  redundant confirm, then stop).
- **Two idle ticks before any stall diagnosis.** A single tick is never
  evidence, and a tick that predates your directive send never counts —
  count only ticks that postdate the send or remain ambiguous. Escalate
  only on task-file-mtime plus sustained-silence
  evidence (see [pact-agent-stall.md](pact-agent-stall.md)).
- **Never accelerate nudging in response to idle ticks.** Patience is the
  counter: faster sends produce the crossed-message rhythm, they do not
  resolve it.

When the notification postdates your directive send, the teammate may not
have acted on it yet — apply the redundant-confirm procedure:

1. **On an idle notification that postdates your directive send**, take a durable read
   of the teammate's task (raw JSON — `TaskGet` is metadata-blind). If the read
   shows the wait resolved AND acted on (e.g., the follow-on task claimed), do
   nothing: the wake landed.
2. **If the durable read shows the wait resolved but not yet acted on (or
   unresolved)**, send exactly ONE redundant confirm naming the actionable durable
   state (e.g., "your teachback on Task A was accepted and A is completed on disk;
   Task B is claimable now"). Name the state,
   not "checking in" — the confirm must let the teammate act from it alone, because
   it may render before your original wake.
3. **Further idle ticks are NOT stalls.** After the one redundant confirm, send no
   further confirms and do not read subsequent idle notifications as stall
   evidence. Escalate to stall diagnosis (see [pact-agent-stall.md](pact-agent-stall.md))
   only on task-file-mtime plus sustained-silence evidence: the task file unchanged
   AND no inbound `SendMessage` from the teammate across multiple idle cycles well
   past your directive send.

**Non-goal — no synchronous hook-based detection.** Do not attempt to close this race with a
synchronous hook, and reject future proposals to reintroduce one: `SendMessage`
fires no PreToolUse/PostToolUse event; inbox files are written asynchronously on
delivery; TeammateIdle notifications fire at turn-end regardless of what has just
landed in the recipient's inbox. Synchronous wake/send detection is therefore
dead-by-construction and historically produced near-100% false positives. The
mitigation layer is: redundant-by-design wake handling on both sides (this rule
plus the teammate-side disk-first re-read in
[pact-agent-teams §On Wake](../skills/pact-agent-teams/SKILL.md#on-wake-disk-first-re-read-seam-agnostic))
plus the duration-keyed `missed_wake_scan` hook that re-surfaces stale
`awaiting_lead_completion` waits at your next user prompt or session start. The same construction bars hook-based content comparison for the deferred audit: the delivered message drains from disk on recipient consumption and survives only in the recipient's conversation context, so no hook running at a later turn boundary can read the message bytes — the deferred disk-vs-message audit is instruction-layer by necessity; reject future proposals to mechanize it. The
two-call atomic pairs above are unchanged by this rule — TaskUpdate-first ordering
stays load-bearing.

---

## Teachback Review

The Task A + Task B dispatch shape gates implementation work behind teachback approval. When dispatching, you create:

- **Task A**: `subject="<role>: TEACHBACK for <feature>"`, owner = teammate. Description states: "Submit TEACHBACK via `metadata.teachback_submit`. SET `intentional_wait{reason=awaiting_lead_completion}`. Do NOT begin Task B."
- **Task B**: `subject="<role>: <primary mission>"`, owner = teammate, `blockedBy=[<Task A id>]`.

Both tasks are created at dispatch time; the teammate receives both in their initial `TaskList` view, with B greyed out by `blockedBy`. **Create Task A FIRST**, before Task B, so the teachback gate gets the LOWER id (creating B first inverts the intuitive "lower id = earlier" reading). Task A's description must NOT name Task B by id — point the teammate at Task B by its subject pattern so A is creatable before B exists. Canonical create sequence: `agents/pact-orchestrator.md` §11 + the command dispatch templates' [Teachback-Gated Dispatch](../commands/orchestrate.md#teachback-gated-dispatch).

**Reviewing the TEACHBACK**:

Read `metadata.teachback_submit` directly:

```
cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{A_id}.json" | jq .metadata.teachback_submit
```

### Read-Trigger Precondition

Before the raw JSON read above is load-bearing, you MUST wait for teammate's wake-signal `SendMessage`. The 3-point rule:

1. **Wake-signal `SendMessage` is the load-bearing content-arrival signal.** The teammate's notify `SendMessage` (sent immediately after their `metadata.teachback_submit` write per [pact-teachback Step 2](../skills/pact-teachback/SKILL.md)) is the only durable signal that the metadata write has landed on disk. Acting on a raw JSON read before that `SendMessage` arrives risks reading empty or stale metadata mid-write.
2. **Raw read MUST follow `SendMessage` receipt, not precede it.** The ordering is: teammate writes `metadata.teachback_submit` → teammate sends notify `SendMessage` → platform poller delivers between-tool-call → your turn opens with the `SendMessage` in context → THEN you read `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{A_id}.json" | jq .metadata.teachback_submit`. Reversing this order produces false-empty reads that have historically triggered false-positive rejection cycles.
3. **Mitigation for residual race.** If your raw read returns empty `{}` immediately after the wake-signal `SendMessage` receipt, the metadata write may still be in flight on the platform side. Mitigations (any one suffices): (a) brief 1-2s delay before re-reading; (b) read twice with a short interval and only treat empty as authoritative if both reads agree; (c) trust the `SendMessage`'s GREEN/RED summary as primary and treat the raw read as audit-only. Do NOT reject a teachback or HANDOFF on a single empty raw read.
4. **Deferred audit — disk vs message.** After acting on a message-carried payload, compare it field-by-field against a fresh raw read of the task metadata at your next turn boundary, BEFORE acting on that teammate's next submission. A disk copy that is missing or diverges from the message copy is a data-integrity finding regardless of origin (the divergence can be a failed disk write OR sender-side composition drift — the channel is faithful, the composer is fallible): record `metadata.integrity_finding` on the affected task and surface it; never reject an already-acted-on submission for it, and never re-attempt it with a hook — the message copy has no durable on-disk home (the inbox drains on delivery), so no later-turn hook can read the message bytes. `metadata.integrity_finding` is advisory by design — no automated reader consumes it; you record the divergence and act on it. When the disk copy is absent, repair it by re-writing the payload to metadata from the message copy while it is still in your context.

The symmetric rule applies to HANDOFF inspection (the raw `cat ... | jq .metadata.handoff` read in §Completion Authority above): wait for teammate's wake-signal `SendMessage` there too before treating the raw read as authoritative. The deferred audit of point 4 applies identically to the HANDOFF path.

The same precondition applies symmetrically to the **rejection-receipt path** (see [Rejection Flow](#rejection-flow) below): the teammate must wait for the lead's wake-signal `SendMessage` notifying of `metadata.teachback_rejection` or `metadata.handoff_rejection` BEFORE reading the rejection metadata via raw JSON. The asymmetry on either side produces the same read-after-write race class.

Compare against the dispatched task description. Apply the validation discipline from [Validating Incoming Teachbacks](#validating-incoming-teachbacks) — check for both misstatements AND omissions.

**Optional audit step** — write a `teachback_resolution` record before flipping status:

```
TaskUpdate(A_id, metadata={"teachback_resolution": {
    "conditions_met": true,
    "resolution_comment": "<optional one-line rationale>"
}})
```

This write is optional but recommended for audit. It is NOT one of the required calls below.

**Approving the TEACHBACK — two-call atomic pair (BOTH required, `TaskUpdate` FIRST)**:

```
TaskUpdate(A_id, status="completed")
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] Teachback accepted on Task #<A_id>. "
        "Task B (#<B_id>) is now claimable."
    ),
    summary="Teachback accepted; Task B claimable"
)
```

The status flip is the load-bearing approval action; the `SendMessage` is the load-bearing wake. Ordering is load-bearing for the same reason as the top-of-file Acceptance pair — `TaskUpdate` first, then the wake-signal `SendMessage` as the last call; if the `TaskUpdate` succeeds and the `SendMessage` errors, retry the send on the tool error.

**Rejecting the TEACHBACK** — see [Rejection Flow](#rejection-flow) below.

> ⚠️ DO NOT mark Task B `completed` and DO NOT mark Task B `pending`. Task B stays `pending` (its initial state) until the teammate claims it (`status=in_progress`) after wake. Your acceptance affects Task A only; Task B's lifecycle is the teammate's to drive (claim → work → submit HANDOFF → idle for your HANDOFF acceptance).

### Validating Incoming Teachbacks

When an agent sends a TEACHBACK, **compare it against the task as you dispatched it — check for both misstatements AND omissions of the objective, constraints, or success criteria**. If you spot a misunderstanding, do NOT accept: write `metadata.teachback_rejection` with the correction and send a correction `SendMessage`. The agent is idling on `awaiting_lead_completion` (blocked, not yet working), so the block holds until you accept — there is no proceed-race; reject and let the agent revise on Task A. Prevents **misunderstanding disguised as agreement** from going undetected until TEST phase. Once decided, follow the [Acceptance or Rejection two-call atomic pair](#completion-authority).

### Directive-Reflection Check

Before acting on ANY teammate protocol-boundary message — a teachback submit, a
HANDOFF notify, a staged-work report you are about to commit, a blocker report —
verify that every directive you sent that teammate MID-TURN (scope amendments,
corrections, re-instructions) is actually reflected in the deliverable the boundary
message describes. Delivery is not processing: a directive delivered while the
teammate was mid-turn does not render in their context until a later turn boundary,
so their boundary message can faithfully describe a deliverable that silently
predates your directive.

- Track outstanding mid-turn directives per teammate (a task-metadata note
  suffices); at each boundary message, tick them off against the reported scope.
  This extends [Validating Incoming Teachbacks](#validating-incoming-teachbacks):
  that check compares the teachback against the task AS DISPATCHED; this check
  compares the deliverable against directives sent AFTER dispatch.
- The staged-work-to-commit boundary is the highest-stakes instance: compare the
  reported stage scope (`git diff --cached --stat`) against every outstanding
  amendment BEFORE committing. A mismatch means hold the commit and re-instruct.
- Teammate-side complement: teammates drain their inbox before composing boundary
  messages and state the drain in the message (see
  [pact-agent-teams §Boundary-Drain Rule](../skills/pact-agent-teams/SKILL.md#boundary-drain-rule)).
  A `boundary-drain: inbox empty` report shifts the residual risk to messages still
  in flight at drain time — your reflection check is the backstop either way, and a
  boundary message with NO drain report is itself a signal to check more carefully.
  The drain report always begins with the literal marker `boundary-drain:`, so its
  presence is mechanically checkable — scan the boundary message text for that
  substring rather than judging from prose whether a drain was reported.

A deliverable that reflects pre-directive scope because your directive crossed it
in flight is a no-fault ordering artifact: reject with corrections naming the
current scope (per [Rejection Flow](#rejection-flow)) — do not treat it as the
teammate misunderstanding the original dispatch.

---

## Rejection Flow

Teachback or HANDOFF inadequate? Reject with **dual-channel delivery** (metadata + `SendMessage`). Same shape for both rejection types:

**Teachback rejection** (`TaskUpdate` FIRST):

```
TaskUpdate(A_id, metadata={"teachback_rejection": {
    "reason": "<one-line summary>",
    "corrections": ["<correction 1>", "<correction 2>", ...],
    "since": "<canonical_since() output>",
    "revision_number": 1
}})
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] Teachback rejected on Task #<A_id>. "
        "REJECTION-PAYLOAD-BEGIN "
        "reason: <one-line summary> "
        "corrections: [<correction 1>, <correction 2>, ...] "
        "since: <canonical_since() output> "
        "revision_number: 1 "
        "REJECTION-PAYLOAD-END "
        "The payload is a verbatim copy of metadata.teachback_rejection. Revise and re-submit. "
        "Task A remains in_progress."
    ),
    summary="Teachback rejected; revise"
)
```

**HANDOFF rejection** (Task B, `TaskUpdate` FIRST):

```
TaskUpdate(B_id, metadata={"handoff_rejection": {
    "reason": "...",
    "corrections": [...],
    "since": "<canonical_since() output>",
    "revision_number": 1
}})
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] HANDOFF rejected on Task #<B_id>. "
        "REJECTION-PAYLOAD-BEGIN "
        "reason: <one-line summary> "
        "corrections: [<correction 1>, <correction 2>, ...] "
        "since: <canonical_since() output> "
        "revision_number: 1 "
        "REJECTION-PAYLOAD-END "
        "The payload is a verbatim copy of metadata.handoff_rejection. Revise."
    ),
    summary="HANDOFF rejected; revise"
)
```

**Why dual-channel**: metadata gives the durable revision spec; the wake-signal `SendMessage` carries that payload verbatim, so the teammate wakes and reads the corrections in the message itself — the disk copy is confirmation, not the primary. Single-channel via metadata only fails because the idle teammate can't self-wake to read it. Single-channel via `SendMessage` only loses durability — the corrections need to survive teammate compaction or agent restart.

**Recovery flow on rejection**:

1. Lead writes rejection metadata + sends wake-signal.
2. Teammate wakes on the message, CLEARs `intentional_wait`, reads the rejection payload the message carries (the disk copy is confirmation).
3. Teammate revises (`metadata.teachback_submit` for A, or revises deliverable + `metadata.handoff` for B).
4. Teammate increments `metadata.revision_number`, sends the notify `SendMessage` carrying the revised payload verbatim, re-SETs `intentional_wait` with a fresh `since` and a `covers_since` equal to it.
5. Lead reviews; either accepts (per [Completion Authority](#completion-authority)) or rejects again (revision_number = N+1).

> **Cycle limit**: 3+ rejection cycles on the same task is an imPACT META-BLOCK signal. See [imPACT.md](../commands/imPACT.md).

---
