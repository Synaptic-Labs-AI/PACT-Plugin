---
name: pact-teachback
description: Command-style teachback protocol for PACT teammates. Invoking this skill directly instructs you to store your teachback in task metadata and idle on awaiting_lead_completion before any implementation work.
---

# Teachback — Store Now

Invoking this skill means you are about to submit a teachback for your
current task. Do not proceed to implementation work until you have stored
it AND received the team-lead's acceptance.

## What a teachback is

A teachback is a Pask Conversation Theory verification gate. Before you
start implementing, you restate your understanding of the task so the
team-lead can catch misunderstandings early, before you burn context on a
wrong implementation.

Under the Task A + Task B dispatch shape, your teachback is the deliverable
of Task A. Task B (the primary work) is `blockedBy=[A]` and stays hidden
in your TaskList until the team-lead accepts your teachback by transitioning
Task A to `completed`.

The baseline 4-field payload (Step 1 below) is the L1 (procedure-level) gate.
At high-variety dispatches, an optional 5th nested field
`reasoning_reconstruction` enables the L1.5 (method-level) gate — see
[pact-ct-teachback.md §When to Method-Reconstruct](../../protocols/pact-ct-teachback.md#when-to-method-reconstruct).
The variety-band thresholds live at the SSOT in `hooks/shared/variety_scorer.py`
(constants `COMPACT_MAX`, `ORCHESTRATE_MAX`, `PLAN_MODE_MAX` and the
`route_workflow` function); do not hard-code the 6 / 10 / 14 thresholds in
skill prose or teachback payloads.

## Action: store teachback now

> **Ordering invariant** (audit anchor): the three steps below MUST execute in the order Step 1 → Step 2 → Step 3 — `metadata.teachback_submit` write FIRST, then notify SendMessage, then `intentional_wait` SET. This ordering is load-bearing for the team-lead's [Read-Trigger Precondition](../../protocols/pact-completion-authority.md#read-trigger-precondition): the lead must wait for teammate's wake-signal SendMessage before treating the raw JSON read as authoritative, but the SendMessage is only safe to send AFTER the metadata write has landed on disk. Reversing Step 1 and Step 2 produces false-empty raw reads on the lead side that have triggered false-positive teachback rejection cycles. Reversing Step 2 and Step 3 (idle before SendMessage) silently strands the lead — they will never see the wake-signal because you went idle without sending it. Editors of this skill: do NOT re-order these steps.

**Step 1 — write the teachback to task metadata** (4 fields + 1 optional nested field):

```
TaskUpdate(taskId, metadata={"teachback_submit": {
    "understanding": "<what you understand you're building, key constraints, interfaces>",
    "most_likely_wrong": "<the part of your understanding you are least confident about>",
    "least_confident_item": "<one specific assumption you'd like the team-lead to confirm>",
    "first_action": "<the first concrete step you will take after teachback approval>",

    # OPTIONAL: include per §When to Method-Reconstruct in pact-ct-teachback.md
    # (required at variety >= 11; recommended at 7-10; skipped at 4-6)
    "reasoning_reconstruction": {
        "decision_attribution": "I understand <upstream agent> chose <decision> because <their stated reason>",
        "assumption_trace":     "This reasoning depends on <assumption A>, <assumption B>, ...",
        "contingency_clause":   "If <assumption A or B> changes, the decision should change to <alternative>"
    }
}})
```

**Step 2 — notify the team-lead** (lightweight prose, NOT the full payload):

```
SendMessage(
    to="team-lead",
    message=(
        "[<your-agent-name>→team-lead] Teachback submitted on Task #<A_id>. "
        "See metadata.teachback_submit. Idling on awaiting_lead_completion."
    ),
    summary="Teachback submitted: <topic>"
)
```

**Step 3 — SET `intentional_wait` and idle**:

```
TaskUpdate(taskId, metadata={"intentional_wait": {
    "reason": "awaiting_lead_completion",
    "expected_resolver": "lead",
    "since": "<canonical_since() output: tz-aware ISO-8601 UTC>"
}})
```

Do NOT begin Task B until Task A's status transitions to `completed`. The team-lead's wake-signal SendMessage confirms acceptance — you cannot self-wake to poll TaskList while idle.

**On rejection** (team-lead writes `metadata.teachback_rejection`): see [pact-agent-teams §On Rejection](../pact-agent-teams/SKILL.md#on-rejection-wake-signal-receipt).

## When to include reasoning_reconstruction

Include the nested `reasoning_reconstruction` sub-object whenever the dispatching task's variety score is **11 or higher** (`ROUTE_PLAN_MODE` or `ROUTE_RESEARCH_SPIKE` per `hooks/shared/variety_scorer.py`). At variety 7-10 (`ROUTE_ORCHESTRATE`) it is **recommended but not required** — the team-lead may SendMessage requesting reconstruction on follow-up if upstream decisions are non-trivial. At variety 4-6 (`ROUTE_COMPACT`) it is **skipped** — absence is the expected default.

The three sub-keys are three distinct cognitive operations on the upstream agent's HANDOFF: `decision_attribution` restates what the upstream decided and their stated reason, `assumption_trace` lists the falsifiable propositions the upstream's reasoning depends on, and `contingency_clause` names a concrete alternative if those assumptions are false. Vague answers ("the architect chose this because it makes sense", "we'd need to redo it") are lead-side reject signals. See [pact-ct-teachback.md §When to Method-Reconstruct](../../protocols/pact-ct-teachback.md#when-to-method-reconstruct) for the full variety-band gate.

If you are dispatched as an owner in `TEACHBACK_EXEMPT_AGENT_TYPES` (currently `{pact-secretary}` per `hooks/shared/intentional_wait.py`), the entire teachback gate is bypassed — including this sub-field. No carve-out logic needed.

## Ordering rule

You must store your teachback (`metadata.teachback_submit` write) before any Edit/Write/Bash call used for implementation work. Reading files to understand the task (Read, Glob, Grep) is permitted before teachback; those are understanding actions, not implementation actions.

Under the Task A + Task B dispatch shape, this ordering is structurally reinforced: Task B is hidden behind `blockedBy=[A]` until Task A's status transitions to `completed`. The `metadata.teachback_submit` write IS your teachback delivery; the team-lead's `TaskUpdate(A, status="completed")` paired with a wake-signal SendMessage IS approval.

## Post-store behavior

Idle on `awaiting_lead_completion` until the team-lead's wake-signal arrives. Do NOT speculatively begin Task B; the team-lead's status flip is the gate.

If you have other claimable, unblocked tasks unrelated to this dispatch (a separate Task A from a different mission), you may claim and work them. The wait is per-task, not per-agent.

## Exception

Consultant questions (a peer asks you something) do not require a teachback. You only teachback on task dispatches.
