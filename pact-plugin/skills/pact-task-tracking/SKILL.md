---
name: pact-task-tracking
description: |
  Task tracking and communication protocol for PACT specialist agents operating
  as Agent Teams teammates. Auto-loaded via agent frontmatter. Defines task
  self-management, message-based communication, and completion workflows.
---

# Task Tracking Protocol

> **Architecture**: See [pact-task-hierarchy.md](../../protocols/pact-task-hierarchy.md) for the full hierarchy model.

## Task Self-Management

You have direct access to Task tools. Use them to manage your own work:

- **TaskUpdate**: Claim unassigned tasks in your domain, mark tasks in-progress or complete.
- **TaskList**: View team progress and discover unassigned tasks.
- **TaskCreate**: Create sub-tasks if your work expands beyond the original scope.

### Conventions

- The team lead is always named **"team-lead"**.
- Claim only tasks within your specialist domain. Do not claim cross-domain tasks.
- When claiming an unassigned task, use `TaskUpdate(taskId, status: "in_progress")`.
- When your task is done, use `TaskUpdate(taskId, status: "completed")` as part of your HANDOFF delivery (see section 8).

## Progress Reporting

Report progress naturally in your responses. For significant milestones, update your task status via TaskUpdate. You do not need to send progress messages to the lead unless you have a specific question or finding to share.

## On Blocker

If you cannot proceed:

1. **Stop work immediately.**
2. Send the blocker to the lead:
   ```
   SendMessage(type: "message", recipient: "team-lead",
     content: "BLOCKER: {description of what is blocking you}\n\nPartial HANDOFF:\n1. Produced: {files}\n2. Key decisions: {decisions}\n3. Areas of uncertainty: {uncertainties}\n4. Integration points: {points}\n5. Open questions: {questions}",
     summary: "BLOCKER: {brief description}")
   ```
3. Do not attempt to work around the blocker. The lead will triage and resolve it.

## On Algedonic Signal

When you detect a viability threat (security, data integrity, ethics), the delivery method depends on the signal level.

See the [algedonic protocol](../../protocols/algedonic.md) for trigger categories and severity guidance.

### HALT Signal (Dual-Delivery)

HALT signals (SECURITY, DATA, ETHICS) must reach both the lead AND all peers. Use two SendMessage calls:

1. **Stop work immediately.**
2. **Direct message to lead** (guaranteed delivery):
   ```
   SendMessage(type: "message", recipient: "team-lead",
     content: "⚠️ ALGEDONIC HALT: {CATEGORY}\n\n**Issue**: {one-line description}\n**Evidence**: {file, line, what you observed}\n**Impact**: {why this threatens viability}\n**Recommended Action**: {what you suggest}",
     summary: "HALT: {category}")
   ```
3. **Broadcast to all peers** (so they stop work):
   ```
   SendMessage(type: "broadcast",
     content: "⚠️ ALGEDONIC HALT: {CATEGORY}\n\n**Issue**: {one-line description}\n**Evidence**: {file, line, what you observed}\n**Impact**: {why this threatens viability}\n**Recommended Action**: {what you suggest}\n\nSTOP ALL WORK IMMEDIATELY. Await lead instructions.",
     summary: "HALT: {category}")
   ```
4. **Send partial HANDOFF** to the lead via a separate SendMessage (see HANDOFF format in section 8).

### ALERT Signal (Direct Message Only)

ALERT signals (QUALITY, SCOPE, META-BLOCK) go to the lead only. The lead decides whether peers need to be notified.

1. **Stop work immediately.**
2. **Direct message to lead**:
   ```
   SendMessage(type: "message", recipient: "team-lead",
     content: "⚠️ ALGEDONIC ALERT: {CATEGORY}\n\n**Issue**: {one-line description}\n**Evidence**: {file, line, what you observed}\n**Impact**: {why this threatens viability}\n**Recommended Action**: {what you suggest}",
     summary: "ALERT: {category}")
   ```
3. **Send partial HANDOFF** to the lead via a separate SendMessage (see HANDOFF format in section 8).

## Pre-Completion Checklist (Self-Validation)

Before sending your HANDOFF, verify ALL of these are present:

1. [ ] **Produced**: At least one file or artifact listed
2. [ ] **Key decisions**: At least one decision with rationale (or "No decisions required" if truly none)
3. [ ] **Areas of uncertainty**: Prioritized list OR explicit "No areas of uncertainty flagged"
4. [ ] **Integration points**: Components touched OR "None"
5. [ ] **Open questions**: Items listed OR "None"

If any item is missing, add it before completing. Do NOT send an incomplete HANDOFF.

## Memory Preservation

After completing your work but BEFORE sending your HANDOFF message, save your work context:

1. Load the `pact-memory` skill.
2. Save a memory with:
   - `context`: What you were working on and why
   - `goal`: The objective of your task
   - `lessons_learned`: Insights, patterns, things that worked or did not
   - `decisions`: Key choices with rationale
   - `entities`: Components, files, services involved
   - `files`: File paths you created or modified

Do this BEFORE sending your HANDOFF. Memory saves that happen after your process ends may be lost.

## On Completion — HANDOFF (Required)

Every completed task must end with a structured HANDOFF delivered to the lead.

### Format

```
HANDOFF:
1. Produced: Files created/modified
2. Key decisions: Decisions with rationale, assumptions that could be wrong
3. Areas of uncertainty (PRIORITIZED):
   - [HIGH] {description} — Why risky, suggested test focus
   - [MEDIUM] {description}
   - [LOW] {description}
4. Integration points: Other components touched
5. Open questions: Unresolved items
```

All five items are required. Not all priority levels need to be present in Areas of uncertainty. If you have no uncertainties, explicitly state "No areas of uncertainty flagged."

### Delivery

Send the HANDOFF via SendMessage AND mark your task complete:

```
SendMessage(type: "message", recipient: "team-lead",
  content: "HANDOFF:\n1. Produced: ...\n2. Key decisions: ...\n3. Areas of uncertainty: ...\n4. Integration points: ...\n5. Open questions: ...",
  summary: "HANDOFF: {brief description of what was completed}")
```

Then mark the task complete:

```
TaskUpdate(taskId, status: "completed")
```

## Peer Communication Guidance

You can communicate with peer teammates when coordination is needed.

### When to Message a Peer

- Sharing findings directly relevant to a peer's current task
- Coordinating on a shared interface or contract
- Reporting an issue that affects a peer's work

### When NOT to Message a Peer

- General status updates (use TaskUpdate instead)
- Questions that should go to the lead (use SendMessage to "team-lead")
- Anything requiring lead approval or triage

Peer names are visible via TaskList (task owners) or your team configuration.

## Plan Approval Workflow

When you are spawned with `mode="plan"`, you must submit a plan before implementing.

1. **Analyze your task**: Read relevant documentation, understand requirements, identify approach.
2. **Create your plan**: Outline what you will do, in what order, and why.
3. **Submit via ExitPlanMode** and wait for lead approval before implementing.
4. **If rejected**: Revise based on the lead's feedback and resubmit.
