---
description: Recursive nested PACT cycle for complex sub-tasks
argument-hint: [backend|frontend|database|prepare|test|architect] <sub-task description>
---
Run a recursive PACT cycle for this sub-task: $ARGUMENTS

This command instructs the lead to run a **nested P→A→C→T cycle** for a sub-task that is too complex for simple delegation but should remain part of the current feature work. The lead spawns specialists as teammates for each inner phase (inner P→A→C→T) sequentially.

---

## Task Hierarchy

Create a nested Task hierarchy. Sub-scope tasks are children of the outer CODE phase task:

```
1. TaskCreate: Sub-scope task "{verb} {sub-scope}" (child of outer CODE phase task)
2. TaskCreate: Inner phase tasks:
   - "PREPARE: {sub-scope-slug}"
   - "ARCHITECT: {sub-scope-slug}"
   - "CODE: {sub-scope-slug}"
   - "TEST: {sub-scope-slug}"
3. TaskUpdate: Set dependencies:
   - Inner phase-to-phase blockedBy chain (same gating as orchestrate)
   - Outer CODE phase task addBlockedBy = [sub-scope task]
4. TaskUpdate: Sub-scope task status = "in_progress"
5. Execute inner P→A→C→T cycle (same per-phase lifecycle as orchestrate: create phase task → in_progress → spawn teammates → teammate tasks in_progress → completed → phase completed)
6. On completion: Outer CODE phase task unblocked for next sub-scope or scope verification
```

**Example structure (standard):**
```
[Phase] "CODE: user-auth" (outer, blockedBy: sub-scope tasks)
├── [Sub-Scope] "Implement backend API"
│   ├── [Phase] "PREPARE: backend-api"
│   ├── [Phase] "ARCHITECT: backend-api"
│   ├── [Phase] "CODE: backend-api"
│   └── [Phase] "TEST: backend-api"
└── [Sub-Scope] "Implement frontend UI"
    ├── [Phase] "PREPARE: frontend-ui"
    ├── [Phase] "ARCHITECT: frontend-ui"
    ├── [Phase] "CODE: frontend-ui"
    └── [Phase] "TEST: frontend-ui"
```

**Scope-aware naming** (when scope contract is provided):

When a scope contract provides a `scope_id`, prefix all tasks with `[scope:{scope_id}]`:

```
[Phase] "CODE: user-auth" (outer, blockedBy: sub-scope tasks)
├── [Sub-Scope] "[scope:backend-api] Implement backend API"
│   ├── [Phase] "[scope:backend-api] PREPARE: backend-api"
│   ├── [Phase] "[scope:backend-api] ARCHITECT: backend-api"
│   ├── [Phase] "[scope:backend-api] CODE: backend-api"
│   └── [Phase] "[scope:backend-api] TEST: backend-api"
└── [Sub-Scope] "[scope:frontend-ui] Implement frontend UI"
    ├── [Phase] "[scope:frontend-ui] PREPARE: frontend-ui"
    ...
```

Include `scope_id` in task metadata: `{ "scope_id": "backend-api" }`. This enables filtering tasks by scope when aggregating results.

---

## When to Use rePACT

Use `/PACT:rePACT` when:
- A sub-task needs full P→A→C→T treatment (prepare, architect, code, test)
- The sub-task should stay on the current branch (no new branch/PR)
- You are already within a `/PACT:orchestrate` workflow
- The outer ARCHITECT phase (decomposition) has produced scope contracts

**Do not use rePACT when:**
- Sub-task is simple → use `/PACT:comPACT` instead
- Sub-task is a top-level feature → use `/PACT:orchestrate` instead
- You are not in an active orchestration → use `/PACT:orchestrate` instead

---

## Usage Modes

### Single-Domain Nested Cycle

When the sub-task fits within one specialist's domain:

```
/PACT:rePACT backend "implement OAuth2 token refresh mechanism"
```

This runs:
1. **Inner PREPARE**: Backend-focused research (token refresh best practices)
2. **Inner ARCHITECT**: Backend component design (token storage, refresh flow)
3. **Inner CODE**: Backend implementation (spawn backend coder as teammate)
4. **Inner TEST**: Smoke tests for the sub-component

### Multi-Domain Nested Cycle

When the sub-task spans multiple specialist domains:

```
/PACT:rePACT "implement payment processing sub-system"
```

This runs a mini-orchestration:
1. **Assess scope**: Determine which specialists are needed
2. **Inner PREPARE**: Research across relevant domains
3. **Inner ARCHITECT**: Design the sub-system
4. **Inner CODE**: Spawn relevant coders as teammates (may be parallel within phase)
5. **Inner TEST**: Smoke tests for the sub-system

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
/PACT:orchestrate (level 0, nesting_depth: 0 in feature task metadata)
  └── /PACT:rePACT (level 1, nesting_depth: 1 in sub-scope task metadata)
        └── /PACT:rePACT  NOT ALLOWED (nesting_depth >= 1, decomposition blocked)
```

**Enforcement**: When rePACT begins, it checks `nesting_depth` in the feature task metadata. If `nesting_depth >= 1`, decomposition is not attempted. The sub-scope's task metadata is set to `nesting_depth: 1`.

> **Design rationale**: Scope detection is bypassed within sub-scopes, so a sub-scope cannot trigger further decomposition. Level 2 nesting is unreachable by design.

If you hit the nesting limit:
- Simplify the sub-task and use `/PACT:comPACT`
- Or escalate to user for guidance

### Branch Behavior

rePACT operates on the current feature branch. Sub-scopes commit sequentially to the same branch — no branch merging needed.

All commits from all sub-scopes land on the single feature branch in execution order.

---

## Output Conciseness

**Default: Concise output.** User sees nested cycle start/completion, not mini-phase details.

| Internal (do not show) | External (show) |
|----------------------|-----------------|
| Inner phase transitions | `rePACT: backend "OAuth2 token refresh"` |
| Nesting depth calculations | `rePACT complete. Continuing parent.` |
| Phase skip reasoning | (implicit -- just proceed) |

**User can always ask** for nested cycle details (e.g., "What phases ran?" or "Show me the inner architect output").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Starting inner PREPARE phase for the nested cycle..." | (just do it) |
| "The nested cycle has completed successfully..." | `rePACT complete. Continuing parent.` |

**Multi-scope aggregation**: When multiple sub-scopes run sequentially, each sub-scope's handoff is stored in its scope task metadata. The lead uses these during scope verification. Each sub-scope handoff is self-contained (no references to sibling state).

---

## Workflow

### Phase 0: Assess

Before starting, verify:
1. **Nesting depth**: Check `nesting_depth` in feature task metadata. If >= 1, cannot nest further.
2. **Scope appropriateness**: Is this truly a sub-task of the parent?
3. **Domain determination**: Single-domain or multi-domain?

### Phase 1: Inner PREPARE (if needed)

For the sub-task, gather focused context:
- Research specific to the sub-component
- Spawn `pact-preparer` as teammate with sub-scope context
- May be skipped if parent PREPARE phase covered this (apply completeness check)
- Output: Notes integrated into parent preparation or separate `-nested` doc

### Phase 2: Inner ARCHITECT (if needed)

Design the sub-component:
- Spawn `pact-architect` as teammate with sub-scope context + parent architecture
- Component design within the larger architecture
- Interface contracts with parent components
- May be skipped for simple sub-tasks or when scope contract provides sufficient design
- Output: Design notes in `-nested` architecture doc or inline

### Phase 3: Inner CODE

Implement the sub-component:
- Spawn relevant specialist(s) as teammates
- For multi-domain: may spawn multiple specialists in parallel within this inner phase
- Apply S2 coordination (file boundaries) if parallel teammates
- Teammates read scope contract from task metadata via chain-read pattern
- Output: Code + handoff stored in agent task metadata

### Phase 4: Inner TEST

Verify the sub-component:
- Spawn `pact-test-engineer` as teammate
- Smoke tests for the sub-component
- Verify integration with parent components
- Output: Test results in handoff stored in task metadata

### Phase 5: Sub-Scope Completion

Complete the nested cycle for this sub-scope:
1. **Commit**: Create atomic commit(s) for this sub-scope's work on the feature branch
2. **Store handoff**: Write sub-scope handoff into scope task metadata (including contract fulfillment if scope contract was provided)
3. **TaskUpdate**: Sub-scope task status = "completed"
4. **Continue**: Move to next sub-scope or scope verification

---

## Sequential Sub-Scope Execution

When the outer ARCHITECT phase (decomposition) has produced multiple scope contracts, rePACT executes each sub-scope sequentially:

```
For each sub-scope (in dependency order from scope contracts):
    1. Create sub-scope task (child of outer CODE phase task)
    2. Run inner P→A→C→T for this sub-scope
    3. Commit sub-scope work to feature branch
    4. Store handoff + contract fulfillment in scope task metadata
    5. Mark sub-scope task completed
    ↓
After all sub-scopes complete:
    6. Execute Scope Verification Protocol
    7. Outer CODE phase complete → proceed to outer TEST
```

**Dependency ordering**: If scope contracts specify dependency ordering (e.g., database scope before backend scope), execute sub-scopes in that order. Otherwise, follow the order provided by the outer ARCHITECT phase.

---

## Scope Verification

After all sub-scopes complete, the lead executes the [Scope Verification Protocol](../protocols/pact-scope-verification.md). This is still within the outer CODE phase.

The protocol includes:
1. **Contract compatibility**: Spawn architect to verify cross-scope interface alignment
2. **Contract fulfillment**: Lead compares each scope's fulfillment metadata against original contracts
3. **Integration testing** (optional): Spawn test engineer for cross-scope integration tests

On verification failure, route through `/PACT:imPACT` for triage.

After verification passes:
- Commit any verification-phase work (integration test files, etc.)
- S4 Checkpoint: Scopes compatible? Integration clean? Plan viable?
- Outer CODE phase is now complete

---

## Context Inheritance

Nested cycles inherit from parent via the chain-read pattern:
- Sub-scope task's `metadata.upstream_tasks` points to parent feature task and outer phase tasks
- Teammates chain-read parent task metadata for architectural decisions, conventions, and branch context
- Current feature branch (all work on same branch)
- Parent task context and requirements
- Coding conventions established in parent or prior sub-scopes

Nested cycles produce:
- Code committed to current feature branch
- Handoff stored in scope task metadata
- Contract fulfillment stored in scope task metadata (when scope contract provided)

---

## Scope Contract Reception

When the outer ARCHITECT phase (decomposition) provides a **scope contract**, the nested cycle operates scope-aware. Without a contract, rePACT behaves as a standard nested cycle. Contract presence is the mode switch — there are no explicit "modes" to select.

**When a scope contract is provided:**

1. **Identity**: Use the contract's `scope_id` as the scope identifier for all task naming and metadata (see Task Hierarchy above)
2. **Deliverables**: Treat contracted deliverables as the success criteria for Inner CODE and Inner TEST
3. **Interfaces**: Use `imports` to understand what sibling scopes provide; use `exports` to ensure this scope exposes what siblings expect
4. **Shared files constraint**: Do NOT modify files listed in the contract's `shared_files` — these are owned by sibling scopes. Communicate this constraint to all spawned teammates.
5. **Conventions**: Apply any `conventions` from the contract in addition to inherited parent conventions
6. **Handoff**: Include a Contract Fulfillment section in the completion handoff and store in scope task metadata (see After Completion below)

**When no scope contract is provided:** Standard rePACT behavior. No scope-aware naming, no contract fulfillment tracking, no shared file constraints.

See [pact-scope-contract.md](../protocols/pact-scope-contract.md) for the contract format specification.

---

## Relationship to Specialist Autonomy

Specialists can invoke nested cycles autonomously (see Autonomy Charter).
`/PACT:rePACT` is for **lead-initiated** nested cycles.

| Initiator | Mechanism |
|-----------|-----------|
| Specialist discovers complexity | Uses Autonomy Charter (declares, executes, reports) |
| Lead identifies complex sub-task | Uses `/PACT:rePACT` command |

Both follow the same protocol; the difference is who initiates.

---

## Examples

### Example 1: Single-Domain Backend Sub-Task

```
/PACT:rePACT backend "implement rate limiting middleware"
```

Lead runs mini-cycle:
- Inner PREPARE: Research rate limiting patterns (spawn preparer as teammate)
- Inner ARCHITECT: Design middleware structure (spawn architect as teammate)
- Inner CODE: Spawn backend coder as teammate
- Inner TEST: Smoke test rate limiting (spawn test engineer as teammate)
- Commit to feature branch

### Example 2: Multi-Domain Sub-System

```
/PACT:rePACT "implement audit logging sub-system"
```

Lead assesses scope:
- Needs: backend (logging service), database (audit tables), frontend (audit viewer)
- Inner PREPARE: Spawn preparer for cross-domain research
- Inner ARCHITECT: Spawn architect for sub-system design
- Inner CODE: Spawn backend coder, database engineer, frontend coder as teammates (parallel)
- Inner TEST: Spawn test engineer for smoke tests
- Commit to feature branch

### Example 3: Skipping Phases

```
/PACT:rePACT frontend "implement form validation component"
```

If parent already has:
- Validation requirements (skip Inner PREPARE — completeness check passes)
- Component design (skip Inner ARCHITECT — scope contract provides sufficient design)

Then just run Inner CODE and Inner TEST.

---

## Error Handling

**If nesting limit exceeded:**
```
NESTING LIMIT: Cannot invoke rePACT at level 2 (nesting_depth >= 1).
Options:
1. Simplify sub-task and use comPACT
2. Escalate to user for guidance
```

**If sub-task is actually top-level:**
```
SCOPE MISMATCH: This appears to be a top-level feature, not a sub-task.
Consider using /PACT:orchestrate instead.
```

---

## Signal Monitoring

After each teammate dispatch within inner phases:
- Check for blocker/algedonic signals via SendMessage responses
- When teammate reports completion via SendMessage
- On any unexpected teammate stoppage (TeammateIdle or timeout)

On signal detected: Follow Signal Task Handling in CLAUDE.md.

For teammate stall detection and recovery, see [Agent Stall Detection](orchestrate.md#agent-stall-detection).

---

## After Completion

When nested cycle completes (all sub-scopes done + scope verification passes):
1. **TaskUpdate**: All sub-scope tasks status = "completed"
2. **Summarize** what was done across sub-scopes
3. **Report** any decisions that affect the parent task
4. **Continue** with parent orchestration (outer CODE phase now complete)

**Handoff format**: Use the standard 5-item structure (Produced, Key decisions, Areas of uncertainty, Integration points, Open questions).

**Contract-aware handoff** (when scope contract was provided): Store contract fulfillment in scope task metadata:

```json
{
  "contract_fulfillment": {
    "deliverables": [
      {"item": "src/auth/service.ts", "status": "delivered"},
      {"item": "src/auth/refresh.ts", "status": "undelivered", "reason": "deferred"}
    ],
    "exports_actual": ["AuthService", "TokenValidator"],
    "imports_actual": ["UserModel"],
    "deviations": ["Added RefreshConfig not in original contract"]
  }
}
```

The lead uses fulfillment metadata from all sub-scopes to drive scope verification. Keep the fulfillment data factual and concise — the lead only needs to know what matched, what did not, and why.

The parent orchestration resumes with the outer CODE phase complete.
