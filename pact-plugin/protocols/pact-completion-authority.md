## Completion Authority

> **Purpose**: Lead-only completion of teammate-owned tasks. Acceptance is a two-call atomic pair (wake-signal SendMessage FIRST, then status flip); rejection is dual-channel (wake-signal SendMessage FIRST, then metadata write).
>
> **Audience**: PACT team-lead (orchestrator). Teammate-side rules live in [pact-agent-teams §On Completion](../skills/pact-agent-teams/SKILL.md#on-completion--handoff-required) and [pact-agent-teams §On Rejection](../skills/pact-agent-teams/SKILL.md#on-rejection-wake-signal-receipt).

You — the team-lead — are the **only** actor who marks teammate-owned tasks `completed`. Teammates write HANDOFFs to `metadata.handoff`, idle on `intentional_wait{reason=awaiting_lead_completion}`, and wait for your acceptance. The `TaskUpdate(status="completed")` flip is the load-bearing approval action; the paired wake-signal SendMessage is the load-bearing wake.

`blockedBy` is pull-only at the platform level — the platform does NOT push a wake on blocker resolution; `blockedBy` is computed at TaskList query time. Idle teammates cannot self-wake to re-poll, so the wake-signal SendMessage is paired with each metadata or status write that resolves their wait.

### Acceptance — two-call atomic pair (BOTH required, SendMessage FIRST)

1. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Task #<id> accepted. Work complete.", summary="Task accepted")` — wakes the idle teammate so they can claim the next task; writes the wake to the inbox file BEFORE the status flip
2. `TaskUpdate(taskId, status="completed")` — status flip; auto-unblocks any tasks with `blockedBy=[<id>]`

Both calls are **required**. The ordering is load-bearing: `SendMessage` must precede `TaskUpdate` so the lifecycle gate's PostToolUse `_has_paired_sendmessage` scan finds the wake on disk by the time the status flip fires. Reversed ordering produces same-batch races and false-positive `completion_no_paired_send` WARNs even when the pair is structurally correct. Skipping the SendMessage entirely strands the teammate idle on `awaiting_lead_completion` until something else (peer message, your next dispatch) wakes them; `blockedBy` resolution is invisible without the wake.

### Rejection — two-call atomic pair (BOTH required, SendMessage FIRST)

1. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Rejected on Task #<id>. See metadata.{teachback,handoff}_rejection. Revise.", summary="Rejected; revise")` — wakes the teammate so they read the corrections; writes the wake to the inbox file BEFORE the metadata write
2. `TaskUpdate(taskId, metadata={"teachback_rejection": {...}})` (Task A) OR `TaskUpdate(taskId, metadata={"handoff_rejection": {...}})` (Task B) — payload `{reason, corrections, since, revision_number}`

Both calls are **required**, and the ordering matches Acceptance for the same lifecycle-gate reason: SendMessage-first prevents `_has_paired_sendmessage` race conditions on the metadata-write trigger. Skipping the SendMessage leaves the teammate idle on stale `awaiting_lead_completion`, never seeing the corrections — symmetric failure to skipping wake on acceptance. The teammate's `intentional_wait` does not auto-clear when you write rejection metadata; only the wake-signal triggers their CLEAR-and-revise flow. **3+ rejection cycles** on the same task is an imPACT META-BLOCK signal.

**Teammate self-completion carve-outs (predicate-witnessed)** — narrow exemptions where the teammate marks `completed` themselves:

| Carve-out | Trigger | Rule |
|---|---|---|
| Signal-tasks | `metadata.completion_type == "signal"` AND `metadata.type ∈ {"blocker", "algedonic"}` | Auditor + algedonic-emitting agents self-complete; the task IS the signal, no HANDOFF to judge. |
| Memory-save | Owner's team-config `agentType` ∈ `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (currently `{pact-secretary}`) | Secretary self-completes memory-save tasks; team-lead has no acceptance criteria for memory bookkeeping. Resolved via team-config lookup on `member.agentType`, so the carve-out applies regardless of spawn name (`session-secretary`, etc.). |

The canonical predicate `is_self_complete_exempt(task, team_name)` in `shared/intentional_wait.py` witnesses ONLY these two surfaces — pure function for your TaskGet inspection and audit tooling. No hook reads it. Pass `team_name` (read from session context) to get accurate exemption signal for surface 1; surface 2 is independent of `team_name`.

**Related (dispatch surface)**: `member.agentType="pact-secretary"` also gets a dispatch carve-out — no TEACHBACK (single-task dispatch). Third agentType-keyed carve-out, parallel to `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (completion, above) and `WAKE_EXCLUDED_AGENT_TYPES` (wake counting); three frozensets, three behavioral surfaces, fully decoupled. See `agents/pact-orchestrator.md` §11 + `commands/bootstrap.md`.

**Lead-driven force-completion (separate path, not predicate-witnessed)**:

| Path | Trigger | Rule |
|---|---|---|
| imPACT termination | `metadata.terminated == true` | You force-complete an unrecoverable agent's task via `TaskStop` + `TaskUpdate(status="completed", metadata={"terminated": true, "reason": "..."})`. See [imPACT.md](../commands/imPACT.md). The `terminated` marker is recognized directly by audit/inspection; `is_self_complete_exempt` does NOT cover this surface (the team-lead writes status=completed directly). |

**TaskGet metadata-blindness reminder**: `TaskGet` does NOT surface `metadata.handoff`. Read directly:

```
cat ~/.claude/tasks/{team_name}/{taskId}.json | jq .metadata.handoff
```

Inspect the HANDOFF before flipping status. If `metadata.handoff` is missing or empty, do NOT mark the task completed — request the teammate write the HANDOFF first.

---

## Teachback Review

The Task A + Task B dispatch shape gates implementation work behind teachback approval. When dispatching, you create:

- **Task A**: `subject="<role>: TEACHBACK for <feature>"`, owner = teammate. Description states: "Submit TEACHBACK via `metadata.teachback_submit`. SET `intentional_wait{reason=awaiting_lead_completion}`. Do NOT begin Task B."
- **Task B**: `subject="<role>: <primary mission>"`, owner = teammate, `blockedBy=[<Task A id>]`.

Both tasks are created at dispatch time; the teammate receives both in their initial TaskList view, with B greyed out by `blockedBy`.

**Reviewing the TEACHBACK**:

Read `metadata.teachback_submit` directly:

```
cat ~/.claude/tasks/{team_name}/{A_id}.json | jq .metadata.teachback_submit
```

### Read-Trigger Precondition

Before the raw JSON read above is load-bearing, you MUST wait for teammate's wake-signal SendMessage. The 4-point rule:

1. **Wake-signal SendMessage is the load-bearing content-arrival signal.** The teammate's notify SendMessage (sent immediately after their `metadata.teachback_submit` write per [pact-teachback Step 2](../skills/pact-teachback/SKILL.md)) is the only durable signal that the metadata write has landed on disk. Acting on a raw JSON read before that SendMessage arrives risks reading empty or stale metadata mid-write.
2. **Monitor `INBOX_GREW` is an alarm-clock, not a content marker.** When the inbox-watch Monitor fires `INBOX_GREW`, that ends your turn at the next between-tool-call boundary so the platform can deliver the queued SendMessage — but the wake event itself contains no content. See [watch-inbox.md L10+L18](../commands/watch-inbox.md) for the canonical alarm-clock-not-mailbox principle. Do NOT skip ahead to a raw JSON read on `INBOX_GREW`; wait for the SendMessage payload itself to surface in your context.
3. **Raw read MUST follow SendMessage receipt, not precede it.** The ordering is: teammate writes `metadata.teachback_submit` → teammate sends notify SendMessage → Monitor fires `INBOX_GREW` (or platform delivers opportunistically) → your turn opens with the SendMessage in context → THEN you read `cat ~/.claude/tasks/{team_name}/{A_id}.json | jq .metadata.teachback_submit`. Reversing this order produces false-empty reads that have triggered false-positive rejection cycles (see CLAUDE.md pin "Read-after-write race on TaskUpdate metadata writes").
4. **Mitigation for residual race.** If your raw read returns empty `{}` immediately after the wake-signal SendMessage receipt, the metadata write may still be in flight on the platform side. Mitigations (any one suffices): (a) brief 1-2s delay before re-reading; (b) read twice with a short interval and only treat empty as authoritative if both reads agree; (c) trust the SendMessage's GREEN/RED summary as primary and treat the raw read as audit-only. Do NOT reject a teachback or HANDOFF on a single empty raw read.

The symmetric rule applies to HANDOFF inspection (the raw `cat ... | jq .metadata.handoff` read in §Completion Authority above): wait for teammate's wake-signal SendMessage there too before treating the raw read as authoritative.

The same precondition applies symmetrically to the **rejection-receipt path** (see [Rejection Flow](#rejection-flow) below): the teammate must wait for the lead's wake-SendMessage notifying of `metadata.teachback_rejection` or `metadata.handoff_rejection` BEFORE reading the rejection metadata via raw JSON. The asymmetry on either side produces the same read-after-write race class.

Compare against the dispatched task description. Apply the validation discipline from [Validating Incoming Teachbacks](#validating-incoming-teachbacks) — check for both misstatements AND omissions.

**Optional audit step** — write a `teachback_resolution` record before flipping status:

```
TaskUpdate(A_id, metadata={"teachback_resolution": {
    "conditions_met": true,
    "resolution_comment": "<optional one-line rationale>"
}})
```

This write is optional but recommended for audit. It is NOT one of the required calls below.

**Approving the TEACHBACK — two-call atomic pair (BOTH required, SendMessage FIRST)**:

```
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] Teachback accepted on Task #<A_id>. "
        "Task B (#<B_id>) is now claimable."
    ),
    summary="Teachback accepted; Task B claimable"
)
TaskUpdate(A_id, status="completed")
```

The status flip is the load-bearing approval action; the SendMessage is the load-bearing wake. Ordering is load-bearing for the same reason as the top-of-file Acceptance pair — SendMessage-first ensures the lifecycle gate's PostToolUse scan sees the wake on disk before the status flip fires.

**Rejecting the TEACHBACK** — see [Rejection Flow](#rejection-flow) below.

> ⚠️ DO NOT mark Task B `completed` and DO NOT mark Task B `pending`. Task B stays `pending` (its initial state) until the teammate claims it (`status=in_progress`) after wake. Your acceptance affects Task A only; Task B's lifecycle is the teammate's to drive (claim → work → submit HANDOFF → idle for your HANDOFF acceptance).

### Validating Incoming Teachbacks

When an agent sends a TEACHBACK, **compare it against the task as you dispatched it — check for both misstatements AND omissions of the objective, constraints, or success criteria**. If you spot a misunderstanding, reply with a correction via `SendMessage` before any other action — the agent is already working, so the correction window is short. Prevents **misunderstanding disguised as agreement** from going undetected until TEST phase. Once decided, follow the [Acceptance or Rejection two-call atomic pair](#completion-authority).

---

## Rejection Flow

Teachback or HANDOFF inadequate? Reject with **dual-channel delivery** (metadata + SendMessage). Same shape for both rejection types:

**Teachback rejection** (SendMessage FIRST):

```
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] Teachback rejected on Task #<A_id>. "
        "See metadata.teachback_rejection. Revise and re-submit. "
        "Task A remains in_progress."
    ),
    summary="Teachback rejected; revise"
)
TaskUpdate(A_id, metadata={"teachback_rejection": {
    "reason": "<one-line summary>",
    "corrections": ["<correction 1>", "<correction 2>", ...],
    "since": "<canonical_since() output>",
    "revision_number": 1
}})
```

**HANDOFF rejection** (Task B, SendMessage FIRST):

```
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] HANDOFF rejected on Task #<B_id>. "
        "See metadata.handoff_rejection. Revise."
    ),
    summary="HANDOFF rejected; revise"
)
TaskUpdate(B_id, metadata={"handoff_rejection": {
    "reason": "...",
    "corrections": [...],
    "since": "<canonical_since() output>",
    "revision_number": 1
}})
```

**Why dual-channel**: metadata gives the durable revision spec the teammate reads on wake; SendMessage gives the wake itself. Single-channel via metadata only fails because the idle teammate can't self-wake to read it. Single-channel via SendMessage only loses durability — the corrections need to survive teammate compaction or agent restart.

**Recovery flow on rejection**:

1. Lead writes rejection metadata + sends wake-signal.
2. Teammate wakes, CLEARs `intentional_wait`, reads rejection metadata.
3. Teammate revises (`metadata.teachback_submit` for A, or revises deliverable + `metadata.handoff` for B).
4. Teammate re-SETs `intentional_wait` with fresh `since`, increments `metadata.revision_number`, SendMessage notifies team-lead "revised."
5. Lead reviews; either accepts (per [Completion Authority](#completion-authority)) or rejects again (revision_number = N+1).

> **Cycle limit**: 3+ rejection cycles on the same task is an imPACT META-BLOCK signal. See [imPACT.md](../commands/imPACT.md).

---
