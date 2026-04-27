---
name: pact-teachback
description: Command-style teachback protocol for PACT teammates. Invoking this skill directly instructs you to store your teachback in task metadata and idle on awaiting_lead_completion before any implementation work.
---

# Teachback — Store Now

Invoking this skill means you are about to submit a teachback for your
current task. Do not proceed to implementation work until you have stored
it AND received the lead's acceptance.

## What a teachback is

A teachback is a Pask Conversation Theory verification gate. Before you
start implementing, you restate your understanding of the task so the
lead can catch misunderstandings early, before you burn context on a
wrong implementation.

Under the Task A + Task B dispatch shape, your teachback is the deliverable
of Task A. Task B (the primary work) is `blockedBy=[A]` and stays hidden
in your TaskList until the lead accepts your teachback by transitioning
Task A to `completed`.

## Action: store teachback now

**Step 1 — write the teachback to task metadata** (4 fields: the structured payload):

```
TaskUpdate(taskId, metadata={"teachback_submit": {
    "understanding": "<what you understand you're building, key constraints, interfaces>",
    "most_likely_wrong": "<the part of your understanding you are least confident about>",
    "least_confident_item": "<one specific assumption you'd like the lead to confirm>",
    "first_action": "<the first concrete step you will take after teachback approval>"
}})
```

**Step 2 — notify the lead** (lightweight prose, NOT the full payload):

```
SendMessage(
    to="team-lead",
    message=(
        "[<your-agent-name>→lead] Teachback submitted on Task #<A_id>. "
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

Do NOT begin Task B until Task A's status transitions to `completed`. The lead's wake-signal SendMessage confirms acceptance — you cannot self-wake to poll TaskList while idle.

**On rejection** (lead writes `metadata.teachback_rejection`): see [pact-agent-teams §On Rejection](../pact-agent-teams/SKILL.md#on-rejection-wake-signal-receipt).

## Ordering rule

You must store your teachback (`metadata.teachback_submit` write) before any Edit/Write/Bash call used for implementation work. Reading files to understand the task (Read, Glob, Grep) is permitted before teachback; those are understanding actions, not implementation actions.

Under the Task A + Task B dispatch shape, this ordering is structurally reinforced: Task B is hidden behind `blockedBy=[A]` until Task A's status transitions to `completed`. The `metadata.teachback_submit` write IS your teachback delivery; the lead's `TaskUpdate(A, status="completed")` paired with a wake-signal SendMessage IS approval.

## Post-store behavior

Idle on `awaiting_lead_completion` until the lead's wake-signal arrives. Do NOT speculatively begin Task B; the lead's status flip is the gate.

If you have other claimable, unblocked tasks unrelated to this dispatch (a separate Task A from a different mission), you may claim and work them. The wait is per-task, not per-agent.

## Exception

Consultant questions (a peer asks you something) do not require a teachback. You only teachback on task dispatches.
