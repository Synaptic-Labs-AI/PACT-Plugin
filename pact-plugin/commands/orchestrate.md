---
description: Delegate a task to PACT specialist agents
argument-hint: [e.g., implement feature X]
---
Orchestrate specialist PACT agents through the PACT workflow to address: $ARGUMENTS

---

## Task Hierarchy

Create the full Task hierarchy upfront for workflow visibility. Tasks serve as both tracking artifacts and instruction sets (Task-as-instruction model).

```
1. TaskCreate: Feature task "{verb} {feature}"
   metadata: { worktree_path, feature_branch, plan_path, plan_status, nesting_depth: 0, impact_cycles: 0 }
2. TaskCreate: Phase tasks (all upfront):
   - "PREPARE: {feature-slug}"
   - "ARCHITECT: {feature-slug}"
   - "CODE: {feature-slug}"
   - "TEST: {feature-slug}"
3. TaskUpdate: Set phase-to-phase blockedBy chain:
   - ARCHITECT blockedBy PREPARE
   - CODE blockedBy ARCHITECT
   - TEST blockedBy CODE
4. TaskUpdate: Feature task status = "in_progress"
```

**Scoped PACT phases**: When decomposition fires after PREPARE, the standard ARCHITECT and CODE phases handle scoped work — they are NOT skipped. The difference is what happens *inside* them:
- **ARCHITECT** includes decomposition: architect produces scope contracts + boundaries; lead amends with coordination constraints
- **CODE** includes rePACT execution (inner P->A->C->T per sub-scope) + scope verification
- Scope sub-feature tasks are created as children of the CODE phase task during execution

The scoped flow is the same P->A->C->T sequence — no special phase names at any level.

For each phase execution:
```
a. TaskUpdate: phase status = "in_progress", metadata = { phase, s4_checkpoint }
b. Analyze work needed (QDCL for CODE)
c. TaskCreate: agent work task(s) as children of phase
   - subject: short imperative label
   - description: full mission (what to do, acceptance criteria, file scope, constraints)
   - metadata: { phase, scope_id, scope_contract, upstream_tasks, artifact_paths, conventions, coordination }
d. Spawn teammate for each agent task (thin bootstrap prompt)
e. Monitor via SendMessage signals (push-based) and TaskList
f. On specialist completion signal: TaskGet to read handoff metadata, TaskUpdate agent task status = "completed"
g. TaskUpdate: phase status = "completed", metadata += { s4_checkpoint }
```

### Five Metadata Levels

Task metadata is structured across five levels, each stored on the appropriate task:

| Level | Stored On | Key Fields |
|-------|-----------|------------|
| **Feature** | Feature task | `worktree_path`, `feature_branch`, `plan_path`, `plan_status`, `nesting_depth`, `impact_cycles`, `triage_history` |
| **Phase** | Phase tasks | `phase`, `skipped`, `skip_reason`, `skip_context`, `s4_checkpoint` |
| **Agent** | Agent work tasks | `coordination` (file_scope, convention_source, concurrent_with, boundary_note), `nesting_depth`, `parent_feature_task`, `recovery_attempts` |
| **Scope** | Scope sub-feature tasks | `scope_id`, `contract_fulfillment` (deliverables, exports_actual, imports_actual, deviations) |
| **Review** | Review task | `pr_url`, `remediation_cycles`, `findings_path` |

**Skipped phases**: Mark directly `completed` (no `in_progress` -- no work occurs):
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "{reason}"})`
Valid reasons: `"approved_plan_exists"`, `"plan_section_complete"`, `"requirements_explicit"`, `"existing_docs_cover_scope"`, `"trivial_change"`, or custom.
<!-- Skip reason semantics:
  - "approved_plan_exists": Plan exists but completeness not verified (legacy/weak)
  - "plan_section_complete": Plan exists AND section passed completeness check (preferred)
  - "requirements_explicit": Task description contains all needed context
  - "existing_docs_cover_scope": docs/preparation/ or docs/architecture/ already complete
  - "trivial_change": Change too small to warrant this phase
-->

---

## S3/S4 Mode Awareness

This command primarily operates in **S3 mode** (operational control)--executing the plan and coordinating agents. However, mode transitions are important:

| Phase | Primary Mode | Mode Checks |
|-------|--------------|-------------|
| **Before Starting** | S4 | Understand task, assess complexity, check for plans |
| **Context Assessment** | S4 | Should phases be skipped? What's the right approach? |
| **Phase Execution** | S3 | Coordinate agents, track progress, clear blockers |
| **On Blocker** | S4 | Assess before responding--is this operational or strategic? |
| **Between Phases** | S4 | Still on track? Adaptation needed? |
| **After Completion** | S4 | Retrospective--what worked, what didn't? |

When transitioning to S4 mode, pause and ask: "Are we still building the right thing, or should we adapt?"

---

## Responding to Algedonic Signals

For algedonic signal handling (HALT/ALERT responses, algedonic vs imPACT distinction), see [algedonic.md](../protocols/algedonic.md).

---

## Output Conciseness

**Default: Concise output.** The orchestrator's internal reasoning (variety analysis, dependency checking, execution strategy) runs internally. User sees only decisions and key context.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Variety dimension scores, full tables | One-line summary: `Variety: Low (5) -- proceeding with orchestrate` |
| QDCL checklist, dependency analysis | Decision only: `Invoking 2 backend coders in parallel` |
| Phase skip reasoning details | Brief: `Skipping PREPARE/ARCHITECT (approved plan exists)` |

**User can always ask** for details (e.g., "Why that strategy?" or "Show me the variety analysis").

**Narration style**: State decisions, not reasoning process. Minimize commentary.

**Exceptions warranting more detail**:
- Error conditions, blockers, or unexpected issues -- proactively explain what went wrong
- High-variety tasks (11+) -- visible reasoning helps user track complex orchestration

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me assess variety and check for the approved plan" | (just do it, show result) |
| "I'm now going to invoke the backend coder" | `Spawning backend coder` |
| "S4 Mode -- Task Assessment" | (implicit, don't announce) |

---

## Before Starting

### Task Variety Assessment

Before running orchestration, assess task variety using the protocol in [pact-variety.md](../protocols/pact-variety.md).

**Quick Assessment Table**:

| If task appears... | Variety Level | Action |
|-------------------|---------------|--------|
| Single file, one domain, routine | Low (4-6) | Offer comPACT using `AskUserQuestion` tool (see below) |
| Multiple files, one domain, familiar | Low-Medium | Proceed with orchestrate, consider skipping PREPARE |
| Multiple domains, some ambiguity | Medium (7-10) | Standard orchestrate with all phases |
| Greenfield, architectural decisions, unknowns | High (11-14) | Recommend plan-mode first |
| Novel technology, unclear requirements, critical stakes | Extreme (15-16) | Recommend research spike before planning |

**Variety Dimensions** (score 1-4 each, sum for total):
- **Novelty**: Routine (1) -> Unprecedented (4)
- **Scope**: Single concern (1) -> Cross-cutting (4)
- **Uncertainty**: Clear (1) -> Unknown (4)
- **Risk**: Low impact (1) -> Critical (4)

**Output format**: One-line summary only. Example: `Variety: Medium (8) -- standard orchestrate with all phases`

**When uncertain**: Default to standard orchestrate. Variety can be reassessed at phase transitions.

**User override**: User can always specify their preferred workflow regardless of assessment.

### Offering comPACT for Low-Variety Tasks

When variety is Low (4-6), offer the user a choice using `AskUserQuestion` tool:

```
AskUserQuestion(
  question: "This task appears routine. Which workflow?",
  options: ["comPACT (Recommended)", "Full orchestrate"]
)
```

If comPACT selected, hand off to `/PACT:comPACT`.

### Team Creation

Create the session team before dispatching any specialists:

```
TeamCreate(name="{feature-slug}")
```

The team persists for the duration of orchestration. All specialists are spawned into this team.

---

## Execution Philosophy

**MANDATORY: Invoke concurrently unless blocked.** The burden of proof is on sequential dispatch. If you cannot cite a specific file conflict or data dependency, you MUST invoke them concurrently.

This applies across ALL phases, not just CODE:
- PREPARE with multiple research areas -> multiple preparers at once
- ARCHITECT with independent component designs -> multiple architects simultaneously
- CODE with multiple domains or independent tasks -> multiple coders together
- TEST with independent test suites -> multiple test engineers concurrently

Sequential execution is the exception requiring explicit justification. When assessing any phase, ask: "Can specialists be invoked concurrently?" The answer is usually yes.

---

1. **Set up worktree**: If already in a worktree for this feature, reuse it. Otherwise, invoke `/PACT:worktree-setup` with the feature branch name. This creates both the feature branch and its worktree. All subsequent phases work in the worktree.
2. **Check for plan** in `docs/plans/` matching this task

### Plan Status Handling

| Status | Action |
|--------|--------|
| PENDING APPROVAL | `/PACT:orchestrate` = implicit approval -> update to IN_PROGRESS |
| APPROVED | Update to IN_PROGRESS |
| BLOCKED | Ask user to resolve or proceed without plan |
| IN_PROGRESS | Confirm: continue or restart? |
| SUPERSEDED/IMPLEMENTED | Confirm with user before proceeding |
| No plan found | Proceed--phases will do full discovery |

---

## Context Assessment

Before executing phases, assess which are needed based on existing context:

| Phase | Run if... | Skip if... |
|-------|-----------|------------|
| **PREPARE** | Requirements unclear, external APIs to research, dependencies unmapped | Approved plan with complete Preparation section (passes completeness check below); OR requirements explicit in task; OR existing `docs/preparation/` covers scope with no unresolved items |
| **ARCHITECT** | New component or module, interface contracts undefined, architectural decisions required | Approved plan with complete Architecture section (passes completeness check below); OR following established patterns with no new components; OR `docs/architecture/` covers design with no open items |
| **CODE** | Always run | Never skip |
| **TEST** | Integration/E2E tests needed, complex component interactions, security/performance verification | ALL of the following are true: (1) trivial change with no new logic requiring tests, (2) no integration boundaries crossed, (3) isolated change with no meaningful test scenarios, AND (4) plan's Phase Requirements section does not mark TEST as REQUIRED (if plan exists) |

**Conflict resolution**: When both "Run if" and "Skip if" criteria apply, **run the phase** (safer default). Example: A plan exists but requirements have changed--run PREPARE to validate.

**Plan-aware fast path**: When an approved plan exists in `docs/plans/`, PREPARE and ARCHITECT are typically skippable--the plan already synthesized specialist perspectives. Skip unless scope has changed, plan appears stale (typically >2 weeks; ask user to confirm if uncertain), OR the plan contains incompleteness signals for that phase (see Phase Skip Completeness Check below).

**State your assessment briefly.** Example: `Skipping PREPARE/ARCHITECT (approved plan exists). Running CODE and TEST.`

The user can override your assessment or ask for details.

### Phase Skip Completeness Check

**Principle: Existence != Completeness.**

Before skipping, scan the plan section for incompleteness signals (see [pact-completeness.md](../protocols/pact-completeness.md)):
- [ ] No unchecked research items (`- [ ]`)
- [ ] No TBD values in decision tables
- [ ] No `Warning: Handled during {PHASE_NAME}` forward references
- [ ] No unchecked questions to resolve
- [ ] No empty/placeholder sections
- [ ] No unresolved open questions

**All clear** -> Skip with reason `"plan_section_complete"` (not `"approved_plan_exists"`)
**Any signal present** -> Run the phase

> **Note**: The plan's Phase Requirements table is advisory. When in doubt, verify against actual section content -- the table may be stale if the plan was updated after initial synthesis.

**Scope detection**: After PREPARE completes (or is skipped), scope detection evaluates whether the task warrants decomposition into sub-scopes. See [Scope Detection Evaluation](#scope-detection-evaluation) below.

---

## Handling Decisions When Phases Were Skipped

When a phase is skipped but a coder encounters a decision that would have been handled by that phase:

| Decision Scope | Examples | Action |
|----------------|----------|--------|
| **Minor** | Naming conventions, local file structure, error message wording | Coder decides, documents in commit message |
| **Moderate** | Interface shape within your module, error handling pattern, internal component boundaries | Coder decides and implements, but flags decision with rationale in handoff; orchestrator validates before next phase |
| **Major** | New module needed, cross-module contract, architectural pattern affecting multiple components | Blocker -> `/PACT:imPACT` -> may need to run skipped phase |

**Boundary heuristic**: If a decision affects files outside the current specialist's scope, treat it as Major.

**Coder instruction when phases were skipped**:

> "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the handoff so it can be validated. Major decisions affecting other components are blockers--don't implement, escalate."

---

### PREPARE Phase -> `pact-preparer`

**Skip criteria met (including completeness check)?** -> Proceed to ARCHITECT phase.

**Plan sections to pass** (if plan exists):
- "Preparation Phase"
- "Open Questions > Require Further Research"

**Create agent work task** (Task-as-instruction):
```
TaskCreate(
  subject: "Research: {feature-slug}",
  description: "Full mission — task description, acceptance criteria, what to research, output expectations",
  metadata: {
    phase: "PREPARE",
    upstream_tasks: [feature_task_id],
    artifact_paths: ["docs/preparation/"],
    plan_reference: "docs/plans/{slug}-plan.md"
  }
)
```

**Spawn teammate** with thin bootstrap prompt:
```
Task(
  name="preparer-1",
  team_name="{feature-slug}",
  prompt="You are a pact-preparer. You have been assigned task {task_id}. Read it with TaskGet and execute it. When done, store your handoff in task metadata via TaskUpdate and send a completion signal via SendMessage to the lead."
)
```

**Before next phase**:
- [ ] Outputs exist in `docs/preparation/`
- [ ] Specialist handoff received (via SendMessage signal + TaskGet for metadata)
- [ ] If blocker reported -> `/PACT:imPACT`
- [ ] **S4 Checkpoint** (see [pact-s4-checkpoints.md](../protocols/pact-s4-checkpoints.md)): Environment stable? Model aligned? Plan viable?

**Concurrent dispatch within PREPARE**: If research spans multiple independent areas (e.g., "research auth options AND caching strategies"), create separate agent work tasks and spawn multiple preparers with clear scope boundaries.

---

### Post-PREPARE Re-assessment

If PREPARE ran and ARCHITECT was marked "Skip," compare PREPARE's recommended approach to the skip rationale:

- **Approach matches rationale** -> Skip holds
- **Novel approach** (new components, interfaces, expanded scope) -> Override, run ARCHITECT

**Example**:
> Skip rationale: "following established pattern in `src/utils/`"
> PREPARE recommends "add helper to existing utils" -> Skip holds
> PREPARE recommends "new ValidationService class" -> Override, run ARCHITECT

---

### Scope Detection Evaluation

After PREPARE completes (or is skipped with plan context), evaluate whether the task warrants decomposition into sub-scopes. For heuristic definitions and scoring, see [pact-scope-detection.md](../protocols/pact-scope-detection.md).

**When**: After PREPARE output is available (or plan content, if PREPARE was skipped). comPACT bypasses detection entirely.

**Process**:
1. Score the task against the heuristics table in the protocol
2. Apply counter-signals to adjust the score downward
3. Determine tier:

| Result | Action |
|--------|--------|
| Score below threshold | Single scope -- continue with standard execution |
| Score at/above threshold | Propose decomposition (see Evaluation Response below) |
| All strong signals fire, no counter-signals, autonomous enabled | Auto-decompose (see Evaluation Response below) |

**Output format**: `Scope detection: Single scope (score 2/3 threshold)` or `Scope detection: Multi-scope detected (score 4/3 threshold) -- proposing decomposition`

#### Evaluation Response

When detection fires (score >= threshold), follow the evaluation response protocol in [pact-scope-detection.md](../protocols/pact-scope-detection.md) -- S5 confirmation flow, user response mapping, and autonomous tier.

**On confirmed decomposition**: ARCHITECT and CODE phases proceed normally (they are NOT skipped), but with decomposition awareness:
- **ARCHITECT**: Architect produces scope contracts and decomposition boundaries in addition to overall architecture. Lead amends contracts with coordination constraints (shared_files restrictions, dependency ordering). See [pact-scope-contract.md](../protocols/pact-scope-contract.md) for the contract format and generation process.
- **CODE**: Lead executes `/PACT:rePACT` for sequential sub-scope execution, then runs Scope Verification Protocol. See [CODE Phase (Scoped Path)](#code-phase-scoped-path) below.

---

### ARCHITECT Phase -> `pact-architect`

**Skip criteria met (including completeness check, after re-assessment)?** -> Proceed to CODE phase.

**Plan sections to pass** (if plan exists):
- "Architecture Phase"
- "Key Decisions"
- "Interface Contracts"

**Create agent work task** (Task-as-instruction):
```
TaskCreate(
  subject: "Design: {feature-slug}",
  description: "Full mission — design components, define interfaces, make architectural decisions. Read upstream PREPARE task {prepare_task_id} for research context. If decomposition is active: also produce scope contracts and decomposition boundaries.",
  metadata: {
    phase: "ARCHITECT",
    upstream_tasks: [prepare_task_id],
    artifact_paths: ["docs/preparation/", "docs/architecture/"],
    plan_reference: "docs/plans/{slug}-plan.md",
    decomposition_active: true/false
  }
)
```

**Spawn teammate** with thin bootstrap prompt:
```
Task(
  name="architect-1",
  team_name="{feature-slug}",
  prompt="You are a pact-architect. You have been assigned task {task_id}. Read it with TaskGet and execute it. When done, store your handoff in task metadata via TaskUpdate and send a completion signal via SendMessage to the lead."
)
```

**If decomposition is active**: After architect completes, lead amends scope contracts with coordination constraints (shared_files restrictions, dependency ordering) and writes them into scope task metadata. The architect produces contracts as S1 design work; the lead adds S2/S3 coordination constraints.

**Before next phase**:
- [ ] Outputs exist in `docs/architecture/`
- [ ] Specialist handoff received (via SendMessage signal + TaskGet for metadata)
- [ ] If blocker reported -> `/PACT:imPACT`
- [ ] **S4 Checkpoint**: Environment stable? Model aligned? Plan viable?

**Concurrent dispatch within ARCHITECT**: If designing multiple independent components (e.g., "design user service AND notification service"), create separate agent work tasks and spawn multiple architects simultaneously. Ensure interface contracts between components are defined as a coordination checkpoint.

---

### CODE Phase -> `pact-*-coder(s)`

**Always runs.** This is the core work.

> **S5 Policy Checkpoint (Pre-CODE)**: Before spawning coders, verify:
> 1. "Does the architecture align with project principles?"
> 2. "Am I delegating ALL code changes to specialists?" (orchestrator writes no application code)
> 3. "Are there any S5 non-negotiables at risk?"
>
> **Delegation reminder**: Even if you identified the exact implementation during earlier phases, you must delegate the actual coding. Knowing what to build != permission to build it yourself.

**Plan sections to pass** (if plan exists):
- "Code Phase"
- "Implementation Sequence"
- "Commit Sequence"

**Select coder(s)** based on scope:
- `pact-backend-coder` -- server-side logic, APIs
- `pact-frontend-coder` -- UI, client-side
- `pact-database-engineer` -- schema, queries, migrations

#### Standard Path (Non-Scoped)

##### Invoke Concurrently by Default

**Default stance**: Dispatch specialists together unless proven dependent. Sequential requires explicit justification.

**Required decision output** (no exceptions):
- "**Concurrent**: [groupings]" -- the expected outcome
- "**Sequential because [specific reason]**: [ordering]" -- requires explicit justification
- "**Mixed**: [concurrent groupings], then [sequential dependencies]" -- when genuinely mixed

**Deviation from concurrent dispatch requires articulated reasoning.** "I'm not sure" defaults to concurrent with S2 coordination, not sequential.

**Analysis should complete quickly.** Use the Quick Dependency Checklist (QDCL) below. If QDCL analysis takes more than 2 minutes, you're likely over-analyzing independent tasks--default to concurrent dispatch with S2 coordination.

##### Execution Strategy Analysis

**REQUIRED**: Complete the QDCL internally before spawning coders.

**Quick Dependency Checklist (QDCL)** -- run mentally, don't output:

For each pair of work units, check:
- Same file modified? -> Sequential (or define strict boundaries)
- A's output is B's input? -> Sequential (A first)
- Shared interface undefined? -> Define interface first, then concurrent
- None of above? -> Concurrent

**Output format**: Decision only. Example: `Spawning backend + frontend coders in parallel` or `Sequential: database first, then backend (schema dependency)`

**If QDCL shows no dependencies**: Concurrent is your answer. Don't second-guess.

##### S2 Pre-Dispatch Coordination

Before concurrent dispatch, check internally: shared files? shared interfaces? conventions established?

- **Shared files**: Sequence those agents OR assign clear boundaries
- **Conventions**: First agent's choice becomes standard; propagate to others
- **Resolution authority**: Technical disagreements -> Architect arbitrates; Style/convention -> First agent's choice

**Output**: Silent if no conflicts; only mention if conflicts found (e.g., `S2 check: types.ts shared -- backend writes, frontend reads`).

##### Dispatch

**Create agent work task(s)** (Task-as-instruction):
```
TaskCreate(
  subject: "Implement: {component}",
  description: "Full mission — what to implement, acceptance criteria, file scope. Read upstream ARCHITECT task {architect_task_id} for design context. Smoke test: run test suite before completing; fix any tests your changes break.",
  metadata: {
    phase: "CODE",
    upstream_tasks: [architect_task_id],
    artifact_paths: ["docs/architecture/"],
    plan_reference: "docs/plans/{slug}-plan.md",
    coordination: {
      file_scope: ["src/auth/**"],
      concurrent_with: [other_task_ids],
      boundary_note: "Do not modify files outside your scope"
    },
    conventions: { ... }
  }
)
```

If PREPARE/ARCHITECT were skipped, include in description: "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the handoff so it can be validated. Major decisions affecting other components are blockers--don't implement, escalate."

**Spawn teammate(s)** with thin bootstrap prompt:
```
Task(
  name="{coder-type}-1",
  team_name="{feature-slug}",
  prompt="You are a {agent-type}. You have been assigned task {task_id}. Read it with TaskGet and execute it. You are working in a git worktree at {worktree_path}. All file paths must be absolute and within this worktree. When done, store your handoff in task metadata via TaskUpdate and send a completion signal via SendMessage to the lead."
)
```

**Include worktree path in all agent task descriptions**: "You are working in a git worktree at {worktree_path}. All file paths must be absolute and within this worktree."

**Include for concurrent specialists** in task description: "You are working concurrently with other specialists. Your scope is {file_scope}. Do not modify files outside your scope."

**Before next phase**:
- [ ] Implementation complete
- [ ] All tests passing (full test suite; fix any tests your changes break)
- [ ] Specialist handoff(s) received (via SendMessage signal + TaskGet for metadata)
- [ ] If blocker reported -> `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of CODE phase work (preserves work before strategic re-assessment)
- [ ] **S4 Checkpoint**: Environment stable? Model aligned? Plan viable?

#### CODE Phase (Scoped Path)

When decomposition is active (scope detection fired and ARCHITECT produced scope contracts):

**1. Sub-scope execution via rePACT**

For each sub-scope sequentially:
```
a. TaskCreate: Sub-feature task as child of CODE phase task
   subject: "Scope: {scope_id}"
   metadata: {
     scope_id: "{scope_id}",
     scope_contract: { ... },  // from architect output, amended by lead
     upstream_tasks: [architect_task_id, feature_task_id],
     nesting_depth: 1,
     parent_feature_task: feature_task_id
   }
b. Execute /PACT:rePACT for this sub-scope (inner P->A->C->T)
   - rePACT spawns specialists into the same team for the inner cycle
   - On completion: commit sub-scope work
```

**2. Scope Verification Protocol**

After all sub-scopes complete, execute the [Scope Verification Protocol](../protocols/pact-scope-verification.md):

```
a. Spawn architect for cross-scope contract compatibility verification
b. Lead verifies contract fulfillment (metadata comparison — orchestrator-level work)
c. Optionally spawn test engineer for cross-scope integration tests (parallel with step a)
```

On verification pass: CODE phase complete -> proceed to TEST.
On verification failure: Route through `/PACT:imPACT` for triage.

**Before next phase** (same as standard path):
- [ ] All sub-scopes implemented and committed
- [ ] Scope verification passed
- [ ] Specialist handoff(s) received
- [ ] If blocker reported -> `/PACT:imPACT`
- [ ] **Create atomic commit(s)** if any verification-phase work produced
- [ ] **S4 Checkpoint**: Scopes compatible? Integration clean? Plan viable?

#### Handling Complex Sub-Tasks During CODE

If a sub-task emerges that is too complex for a single specialist:

| Sub-Task Complexity | Indicators | Use |
|---------------------|------------|-----|
| **Simple** | Code-only, clear requirements | Direct specialist spawn |
| **Focused** | Single domain, no research needed | `/PACT:comPACT` |
| **Complex** | Needs own P->A->C->T cycle | `/PACT:rePACT` |

**When to use `/PACT:rePACT`:**
- Sub-task needs its own research/preparation phase
- Sub-task requires architectural decisions before coding
- Sub-task spans multiple concerns within a domain

**Phase re-entry** (via `/PACT:imPACT`): When imPACT decides to redo a prior phase, create a new retry phase task -- do not reopen the completed one. See [imPACT.md Phase Re-Entry Task Protocol](imPACT.md#phase-re-entry-task-protocol) for details.

---

### TEST Phase -> `pact-test-engineer`

**Skip criteria met?** -> Proceed to "After All Phases Complete."

**Plan sections to pass** (if plan exists):
- "Test Phase"
- "Test Scenarios"
- "Coverage Targets"

**Create agent work task** (Task-as-instruction):
```
TaskCreate(
  subject: "Test: {feature-slug}",
  description: "Full mission — what to test, test scenarios, coverage targets. Read upstream CODE task(s) for implementation context. You own ALL substantive testing: unit tests, integration, E2E, edge cases.",
  metadata: {
    phase: "TEST",
    upstream_tasks: [code_task_ids],
    artifact_paths: ["src/", "docs/architecture/"],
    plan_reference: "docs/plans/{slug}-plan.md"
  }
)
```

**Spawn teammate** with thin bootstrap prompt:
```
Task(
  name="test-engineer-1",
  team_name="{feature-slug}",
  prompt="You are a pact-test-engineer. You have been assigned task {task_id}. Read it with TaskGet and execute it. You are working in a git worktree at {worktree_path}. All file paths must be absolute and within this worktree. When done, store your handoff in task metadata via TaskUpdate and send a completion signal via SendMessage to the lead."
)
```

**Before completing**:
- [ ] All tests passing
- [ ] Specialist handoff received (via SendMessage signal + TaskGet for metadata)
- [ ] If blocker reported -> `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of TEST phase work (preserves work before strategic re-assessment)

**Concurrent dispatch within TEST**: If test suites are independent (e.g., "unit tests AND E2E tests" or "API tests AND UI tests"), create separate agent work tasks and spawn multiple test engineers with clear suite boundaries.

---

## Agent Stall Detection

For stall detection indicators, recovery protocol, prevention, and non-happy-path task termination, see [pact-agent-stall.md](../protocols/pact-agent-stall.md).

---

## Signal Monitoring

Monitor for blocker/algedonic signals via SendMessage:
- After each teammate spawn
- When teammate reports completion
- On any unexpected teammate idle/termination

On signal detected: Follow Signal Task Handling in CLAUDE.md.

---

## After All Phases Complete

> **S5 Policy Checkpoint (Pre-PR)**: Before creating PR, verify: "Do all tests pass? Is system integrity maintained? Have S5 non-negotiables been respected throughout?"

1. **Update plan status** (if plan exists): IN_PROGRESS -> IMPLEMENTED
2. **Verify all work is committed** -- CODE and TEST phase commits should already exist; if any uncommitted changes remain, commit them now
3. **Clean up team**: `TeamDelete(name="{feature-slug}")` -- terminates all remaining teammates and releases resources
4. **TaskUpdate**: Feature task status = "completed" (all phases done, all work committed)
5. **Run `/PACT:peer-review`** to create PR and get multi-agent review
6. **Present review summary and stop** -- orchestrator never merges (S5 policy)
7. **S4 Retrospective** (after user decides): Briefly note--what worked well? What should we adapt for next time?
8. **High-variety audit trail** (variety 10+ only): Delegate to `pact-memory-agent` to save key orchestration decisions, S3/S4 tensions resolved, and lessons learned
