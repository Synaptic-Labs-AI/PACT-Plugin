---
description: Triage after hitting blocker (Is help and/or a redo needed?)
argument-hint: [e.g., similar errors keep occurring despite attempts to fix]
---
You hit a blocker: $ARGUMENTS

---

## Task Operations

imPACT operates on blocker Tasks reported by agents.

These are orchestrator-side operations (agents report blockers via SendMessage to the lead; the orchestrator manages Tasks):

```
1. TaskGet(blocker_id) — understand the blocker context
2. Triage: redo prior phase? need specialist? need user?
3. On resolution path chosen:
   - If delegating: TaskCreate resolution agent task
   - If self-resolving: proceed directly
4. On resolution complete: TaskUpdate(blocker_id, status="completed")
5. Blocked agent task is now unblocked
```

**Note**: Agents report blockers via SendMessage to the lead ("BLOCKER: {description}"). The orchestrator creates blocker Tasks and uses `addBlockedBy` to block the agent's task. When the blocker is resolved (marked completed), the agent's task becomes unblocked. Exception: `pact-memory-agent` reports blockers via text in its response output (it is not a team member).

---

## Core Principle: Diagnose, Don't Fix

**Your role is triage, not implementation.** Even if you know exactly what's wrong and how to fix it:

1. **Diagnose** — Identify what went wrong (upstream issue? scope mismatch? missing context?)
2. **Determine** — Who should fix it (which specialist?)
3. **Delegate** — What do they need (additional context, parallel support?)

> **Knowing the fix ≠ permission to implement it.**

Common traps to avoid:
- "I can see exactly what's wrong" — Great diagnosis. Now delegate the fix.
- "Re-delegating seems wasteful" — Role boundaries matter more than perceived efficiency.
- "It's just a small change" — Small changes are still application code. Delegate.

---

## VSM Context: S3 Operational Triage

imPACT is **S3-level triage**—operational problem-solving within normal workflow. It is NOT S5 algedonic escalation (emergency bypass to user).

**imPACT handles**: Blockers that can be resolved by redoing a phase or adding agents.

**Algedonic escalation handles**: Viability threats (security, data, ethics violations). See [algedonic.md](../protocols/algedonic.md).

**Escalation indicator**: If you run 3+ consecutive imPACT cycles without resolution, this may indicate a systemic issue requiring user intervention (proto-algedonic signal).

### imPACT vs Algedonic

| Aspect | imPACT | Algedonic |
|--------|--------|-----------|
| **Level** | S3 (operational) | S5 (policy/viability) |
| **Who decides** | Orchestrator triages | User decides |
| **Question** | "How do we proceed?" | "Should we proceed at all?" |
| **Examples** | Missing info, wrong approach, need help | Security breach, data risk, ethics issue |

**When imPACT becomes Algedonic**:
- 3+ consecutive imPACT cycles without resolution → Emit ALERT (META-BLOCK)
- During imPACT triage, discover viability threat → Emit appropriate HALT/ALERT instead

imPACT is for operational problem-solving. If you're questioning whether the work should continue at all, emit an algedonic signal instead. See [algedonic.md](../protocols/algedonic.md) for trigger conditions and signal format.

---

## Output Conciseness

**Default: Concise output.** User sees triage outcome, not diagnostic process.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Question-by-question analysis | `imPACT: Redo ARCHITECT — interface mismatch` |
| Full diagnostic reasoning | `imPACT: Augmenting with parallel backend coder` |
| Context-gathering details | `imPACT: Not blocked — clarifying guidance to agent` |

**User can always ask** for triage details (e.g., "Why redo that phase?" or "What was the diagnosis?").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me assess whether this is an upstream issue..." | (just do it, report outcome) |
| "The first question is whether we need to redo..." | `imPACT: [outcome] — [reason]` |

---

## Gather Context

Before triaging, quickly check for existing context:
- **Plan**: Check `docs/plans/` for related plan (broader feature context)
- **Prior phase outputs**: Check `docs/preparation/`, `docs/architecture/` for relevant artifacts

This context informs whether the blocker is isolated or systemic.

## Triage

Answer two questions:

1. **Redo prior phase?** — Is the issue upstream in P→A→C→T?
2. **Additional agents needed?** — Do we need help beyond the blocked agent's scope/specialty?

## Outcomes

| Outcome | When | Action |
|---------|------|--------|
| **Redo prior phase** | Issue is upstream in P→A→C→T | Re-delegate to relevant agent(s) to redo the prior phase |
| **Augment present phase** | Need help in current phase | Re-invoke blocked agent with additional context + parallel agents |
| **Invoke rePACT** | Sub-task needs own P→A→C→T cycle | Use `/PACT:rePACT` for nested cycle |
| **Not truly blocked** | Neither question is "Yes" | Instruct agent to continue with clarified guidance |
| **Escalate to user** | 3+ imPACT cycles without resolution | Proto-algedonic signal—systemic issue needs user input |

**When to consider rePACT**:
If the blocker reveals that a sub-task is more complex than expected and needs its own research/design phase, use `/PACT:rePACT` instead of just augmenting:
```
/PACT:rePACT backend "implement the OAuth2 token refresh that's blocking us"
```

---

## Phase Re-Entry Task Protocol

**Context**: Invoked from [orchestrate.md](orchestrate.md) when redoing a prior phase. Complete original blocker first ([Task Operations](#task-operations) step 4). Retry failure triggers 3+ cycle escalation (ALERT: META-BLOCK).

**Worktree context**: Phase re-entry operates within the current worktree. The re-entered phase inherits the same worktree path -- no new worktree is created.

When imPACT decides to redo a prior phase (e.g., "redo ARCHITECT because the design was wrong"), follow this Task lifecycle:

1. **Do NOT reopen the old phase task** — it was completed and is historical record
2. **Create a new retry phase task**: `TaskCreate("ARCHITECT (retry): {feature-slug}")`
3. **Set retry task to `in_progress`**
4. **Block the current phase** (the one that hit the blocker): `TaskUpdate(currentPhaseId, addBlockedBy=[retryPhaseId])`
5. **Dispatch agent(s)** for the retry phase
6. **On retry completion**: `TaskUpdate(retryPhaseId, status="completed")` — unblocks the current phase
7. **Retry the current phase** with a new agent task using the updated outputs
