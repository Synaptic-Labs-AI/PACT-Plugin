---
description: Recursive nested PACT cycle for complex sub-tasks
argument-hint: [backend|frontend|database|prepare|test|architect] <sub-task description>
---
Run a recursive PACT cycle for this sub-task: $ARGUMENTS

This command initiates a **nested P→A→C→T cycle** for a sub-task that is too complex for simple delegation but should remain part of the current feature work.

**Team behavior**: rePACT spawns sub-scope teammates into the existing session team (`pact-{branch-name}`). No new team is created. Use scope-prefixed names (e.g., `backend-coder-auth-scope`) to distinguish sub-scope teammates from parent-scope teammates.

---

## Task Hierarchy

Create a nested Task hierarchy as a child of the current context:

```
1. TaskCreate: Sub-feature task "{verb} {sub-feature}" (child of parent context)
2. TaskCreate: Nested phase tasks:
   - "PREPARE: {sub-feature-slug}"
   - "ARCHITECT: {sub-feature-slug}"
   - "CODE: {sub-feature-slug}"
   - "TEST: {sub-feature-slug}"
3. TaskUpdate: Set dependencies:
   - Phase-to-phase blockedBy chain (same as orchestrate)
   - Parent task addBlockedBy = [sub-feature task]
4. TaskUpdate: Sub-feature task status = "in_progress"
5. Execute nested P→A→C→T cycle (same per-phase lifecycle as orchestrate: create phase task → `in_progress` → dispatch agents → agent tasks `in_progress` → `completed` → phase `completed`)
6. On completion: Parent task unblocked
```

**Example structure (standard):**
```
[Feature] "Implement user auth" (parent, blockedBy: sub-feature)
└── [Sub-Feature] "Implement OAuth2 token refresh"
    ├── [Phase] "PREPARE: oauth2-token-refresh"
    ├── [Phase] "ARCHITECT: oauth2-token-refresh"
    ├── [Phase] "CODE: oauth2-token-refresh"
    └── [Phase] "TEST: oauth2-token-refresh"
```

**Scope-aware naming** (when scope contract is provided):

When a scope contract provides a `scope_id`, prefix all tasks with `[scope:{scope_id}]`:

```
[Feature] "Implement user auth" (parent, blockedBy: sub-feature)
└── [Sub-Feature] "[scope:backend-api] Implement backend API"
    ├── [Phase] "[scope:backend-api] PREPARE: backend-api"
    ├── [Phase] "[scope:backend-api] ARCHITECT: backend-api"
    ├── [Phase] "[scope:backend-api] CODE: backend-api"
    └── [Phase] "[scope:backend-api] TEST: backend-api"
```

Include `scope_id` in task metadata: `{ "scope_id": "backend-api" }`. This enables the parent orchestrator to filter tasks by scope when aggregating results.

---

## When to Use rePACT

Use `/PACT:rePACT` when:
- A sub-task needs full P→A→C→T treatment (prepare, architect, code, test)
- The sub-task should stay on the current branch (no new branch/PR)
- You're already within a `/PACT:orchestrate` workflow

**Don't use rePACT when:**
- Sub-task is simple → use `/PACT:comPACT` instead
- Sub-task is a top-level feature → use `/PACT:orchestrate` instead
- You're not in an active orchestration → use `/PACT:orchestrate` instead

---

## Usage Modes

### Single-Domain Nested Cycle

When the sub-task fits within one specialist's domain:

```
/PACT:rePACT backend "implement OAuth2 token refresh mechanism"
```

This runs:
1. **Mini-Prepare**: Backend-focused research (token refresh best practices)
2. **Mini-Architect**: Backend component design (token storage, refresh flow)
3. **Mini-Code**: Backend implementation
4. **Mini-Test**: Smoke tests for the sub-component

### Multi-Domain Nested Cycle

When the sub-task spans multiple specialist domains:

```
/PACT:rePACT "implement payment processing sub-system"
```

This runs a mini-orchestration:
1. **Assess scope**: Determine which specialists are needed
2. **Mini-Prepare**: Research across relevant domains
3. **Mini-Architect**: Design the sub-system
4. **Mini-Code**: Invoke relevant coders (may be parallel)
5. **Mini-Test**: Smoke tests for the sub-system

---

## Specialist Selection

| Shorthand | Specialist | Use For |
|-----------|------------|---------|
| `backend` | pact-backend-coder | Server-side sub-components |
| `frontend` | pact-frontend-coder | UI sub-components |
| `database` | pact-database-engineer | Data layer sub-components |
| `prepare` | pact-preparer | Research-only nested cycles |
| `test` | pact-test-engineer | Test infrastructure sub-tasks |
| `architect` | pact-architect | Design-only nested cycles |

**If no specialist specified**: Assess the sub-task and determine which specialists are needed (multi-domain mode).

---

## Constraints

### Nesting Depth

**Maximum nesting: 1 level**

```
/PACT:orchestrate (level 0)
  └── /PACT:rePACT (level 1, max)
        └── /PACT:rePACT ← NOT ALLOWED
```

> **Design rationale**: V3 repurposed rePACT as the single-level executor for sub-scopes dispatched by ATOMIZE. Level 2 nesting is unreachable by design -- scope detection is bypassed within sub-scopes, so a sub-scope cannot trigger further decomposition.

If you hit the nesting limit:
- Simplify the sub-task and use `/PACT:comPACT`
- Or escalate to user for guidance

---

## Output Conciseness

**Default: Concise output.** User sees nested cycle start/completion, not mini-phase details.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Mini-phase transitions | `rePACT: backend "OAuth2 token refresh"` |
| Nesting depth calculations | `rePACT complete. Continuing parent.` |
| Phase skip reasoning | (implicit — just proceed) |

**User can always ask** for nested cycle details (e.g., "What phases ran?" or "Show me the mini-architect output").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Starting mini-PREPARE phase for the nested cycle..." | (just do it) |
| "The nested cycle has completed successfully..." | `rePACT complete. Continuing parent.` |

**Multi-scope aggregation**: When the parent orchestrator runs multiple rePACT sub-scopes, each sub-scope's handoff feeds into parent-level aggregation. The sub-scope should keep its handoff self-contained (no references to sibling state). The parent orchestrator is responsible for comparing fulfillment sections across sub-scopes during the consolidate phase.

---

### Branch Behavior

Branch behavior depends on whether rePACT is invoked with a scope contract:

**Without scope contract** (standard nested cycle):
- **No new branch**: rePACT stays on the current feature branch
- **No PR**: Results integrate into the parent task's eventual PR
- All commits remain part of the current feature work

**With scope contract** (from ATOMIZE phase):
- **Receives worktree path** from the parent orchestrator (created by parent via `/PACT:worktree-setup`)
- **Operates in isolated worktree** on a suffix branch (e.g., `feature-X--{scope_id}`)
- **Pass worktree path to all agent prompts**: Include "You are working in a git worktree at [worktree_path]" in specialist dispatches, consistent with orchestrate.md
- All commits stay on the suffix branch within the worktree
- Branch merges back to the feature branch during the CONSOLIDATE phase

---

## Workflow

### Phase 0: Assess

Before starting, verify:
1. **Nesting depth**: Are we within the 1-level limit?
2. **Scope appropriateness**: Is this truly a sub-task of the parent?
3. **Domain determination**: Single-domain or multi-domain?

### Phase 1: Mini-Prepare (if needed)

For the sub-task, gather focused context:
- Research specific to the sub-component
- May be skipped if parent Prepare phase covered this
- Output: Notes integrated into parent preparation or separate `-nested` doc

### Phase 2: Mini-Architect (if needed)

Design the sub-component:
- Component design within the larger architecture
- Interface contracts with parent components
- May be skipped for simple sub-tasks
- Output: Design notes in `-nested` architecture doc or inline

### Phase 3: Mini-Code

Implement the sub-component:

For each specialist needed:
1. `TaskCreate(subject="{scope-prefixed-name}: implement {sub-task}", description="[full CONTEXT/MISSION/INSTRUCTIONS/GUIDELINES]")`
2. `TaskUpdate(taskId, owner="{scope-prefixed-name}")`
3. `Task(name="{scope-prefixed-name}", team_name="pact-{branch}", subagent_type="pact-{specialist-type}", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")`

For multi-domain: spawn multiple specialists in parallel.
Apply S2 coordination if parallel work.
Output: Code + HANDOFF via SendMessage to lead.

### Phase 4: Mini-Test

Verify the sub-component:
- Smoke tests for the sub-component
- Verify integration with parent components
- Output: Test results in handoff

### Phase 5: Integration

Complete the nested cycle:
1. **Verify**: Sub-component works within parent context
2. **Handoff**: Return control to parent orchestration with summary

---

## Context Inheritance

Nested cycles inherit from parent:
- Current feature branch
- Parent task context and requirements
- Architectural decisions from parent
- Coding conventions established in parent

Nested cycles produce:
- Code committed to current branch
- Handoff summary for parent orchestration

---

## Scope Contract Reception

When the parent orchestrator invokes rePACT with a **scope contract** (from scope detection and decomposition), the nested cycle operates scope-aware. Without a contract, rePACT behaves as described above. Contract presence is the mode switch — there are no explicit "modes" to select.

**When a scope contract is provided:**

1. **Identity**: Use the contract's `scope_id` as the scope identifier for all task naming and metadata (see Task Hierarchy above)
2. **Deliverables**: Treat contracted deliverables as the success criteria for Mini-Code and Mini-Test
3. **Interfaces**: Use `imports` to understand what sibling scopes provide; use `exports` to ensure this scope exposes what siblings expect
4. **Shared files constraint**: Do NOT modify files listed in the contract's `shared_files` — these are owned by sibling scopes. Communicate this constraint to all dispatched specialists.
5. **Conventions**: Apply any `conventions` from the contract in addition to inherited parent conventions
6. **Handoff**: Include a Contract Fulfillment section in the completion handoff (see After Completion below)

**When no scope contract is provided:** Standard rePACT behavior. No scope-aware naming, no contract fulfillment tracking, no shared file constraints.

See [pact-scope-contract.md](../protocols/pact-scope-contract.md) for the contract format specification.

---

## Relationship to Specialist Autonomy

Specialists can invoke nested cycles autonomously (see Autonomy Charter).
`/PACT:rePACT` is for **orchestrator-initiated** nested cycles.

| Initiator | Mechanism |
|-----------|-----------|
| Specialist discovers complexity | Uses Autonomy Charter (declares, executes, reports) |
| Orchestrator identifies complex sub-task | Uses `/PACT:rePACT` command |

Both follow the same protocol; the difference is who initiates.

---

## Examples

### Example 1: Single-Domain Backend Sub-Task

```
/PACT:rePACT backend "implement rate limiting middleware"
```

Orchestrator runs mini-cycle:
- Mini-Prepare: Research rate limiting patterns
- Mini-Architect: Design middleware structure
- Mini-Code: Invoke backend coder
- Mini-Test: Smoke test rate limiting

### Example 2: Multi-Domain Sub-System

```
/PACT:rePACT "implement audit logging sub-system"
```

Orchestrator assesses scope:
- Needs: backend (logging service), database (audit tables), frontend (audit viewer)
- Runs mini-orchestration with all three domains
- Coordinates via S2 protocols

### Example 3: Skipping Phases

```
/PACT:rePACT frontend "implement form validation component"
```

If parent already has:
- Validation requirements (skip mini-prepare)
- Component design (skip mini-architect)

Then just run mini-code and mini-test.

---

## Error Handling

**If nesting limit exceeded:**
```
⚠️ NESTING LIMIT: Cannot invoke rePACT at level 2.
Options:
1. Simplify sub-task and use comPACT
2. Escalate to user for guidance
```

**If sub-task is actually top-level:**
```
⚠️ SCOPE MISMATCH: This appears to be a top-level feature, not a sub-task.
Consider using /PACT:orchestrate instead.
```

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **SendMessage**: Teammates send blockers and algedonic signals directly to the lead
- **TaskList**: Check for tasks with blocker metadata or stalled status

On signal detected: Follow Signal Task Handling in CLAUDE.md.

For agent stall detection and recovery, see [Agent Stall Detection](orchestrate.md#agent-stall-detection).

---

## After Completion

When nested cycle completes:
1. **TaskUpdate**: Sub-feature task status = "completed"
2. **Summarize** what was done in the nested cycle
3. **Report** any decisions that affect the parent task
4. **Continue** with parent orchestration (parent task now unblocked)

**Handoff format**: Use the standard 5-item structure (Produced, Key decisions, Areas of uncertainty, Integration points, Open questions).

**Contract-aware handoff** (when scope contract was provided): Append a Contract Fulfillment section after the standard 5-item handoff:

```
Contract Fulfillment:
  Deliverables:
    - ✅ {delivered item} → {actual file/artifact}
    - ❌ {undelivered item} → {reason}
  Interfaces:
    exports: {what was actually exposed}
    imports: {what was actually consumed from siblings}
  Deviations: {any departures from the contract, with rationale}
```

The parent orchestrator uses fulfillment sections from all sub-scopes to drive the consolidate phase. Keep the fulfillment section factual and concise — the parent only needs to know what matched, what didn't, and why.

The parent orchestration resumes with the sub-task complete.
