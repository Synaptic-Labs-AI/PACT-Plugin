---
description: Delegate a task to PACT specialist agents
argument-hint: [e.g., implement feature X]
---
Orchestrate specialist PACT agents through the PACT workflow to address: $ARGUMENTS

---

## Task Hierarchy

Create the full Task hierarchy upfront for workflow visibility:

```
1. TaskCreate: Feature task "{verb} {feature}"
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

**Scoped PACT phases**: When decomposition fires after PREPARE, the standard ARCHITECT and CODE phases are skipped (`decomposition_active`) and replaced by scoped phases. Create retroactively (detection occurs after PREPARE):
- `"ATOMIZE: {feature-slug}"` with `blockedBy = [PREPARE task ID]`
- `"CONSOLIDATE: {feature-slug}"` with `blockedBy = [all scope task IDs]`
- Add CONSOLIDATE to TEST's `blockedBy` via `addBlockedBy = [CONSOLIDATE task ID]` (the original CODE dependency auto-resolves when CODE is marked completed/skipped)

The scoped flow is: **P**repare → **A**tomize → **C**onsolidate → **T**est (same PACT acronym, scoped meanings).

For each phase execution:
```
a. TaskUpdate: phase status = "in_progress"
b. Analyze work needed (QDCL for CODE)
c. TaskCreate: agent task(s) as children of phase
d. TaskUpdate: agent tasks owner = "{agent-name}"
e. TaskUpdate: next phase addBlockedBy = [agent IDs]
f. Spawn teammates: Task(name="{name}", team_name="pact-{branch}", subagent_type="pact-{type}", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")
g. Monitor via SendMessage (HANDOFFs) and TaskList until agents complete
h. TaskUpdate: phase status = "completed" (agents self-manage their task status)
```

**Skipped phases**: Mark directly `completed` (no `in_progress` — no work occurs):
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "{reason}"})`
Valid reasons: `"approved_plan_exists"`, `"plan_section_complete"`, `"requirements_explicit"`, `"existing_docs_cover_scope"`, `"trivial_change"`, `"decomposition_active"`, or custom.
<!-- Skip reason semantics:
  - "approved_plan_exists": Plan exists but completeness not verified (legacy/weak)
  - "plan_section_complete": Plan exists AND section passed completeness check (preferred)
  - "requirements_explicit": Task description contains all needed context
  - "existing_docs_cover_scope": docs/preparation/ or docs/architecture/ already complete
  - "trivial_change": Change too small to warrant this phase
  - "decomposition_active": Scope detection triggered decomposition; sub-scopes handle this phase via rePACT
-->

---

## S3/S4 Mode Awareness

This command primarily operates in **S3 mode** (operational control)—executing the plan and coordinating agents. However, mode transitions are important:

| Phase | Primary Mode | Mode Checks |
|-------|--------------|-------------|
| **Before Starting** | S4 | Understand task, assess complexity, check for plans |
| **Context Assessment** | S4 | Should phases be skipped? What's the right approach? |
| **Phase Execution** | S3 | Coordinate agents, track progress, clear blockers |
| **On Blocker** | S4 | Assess before responding—is this operational or strategic? |
| **Between Phases** | S4 | Still on track? Adaptation needed? |
| **After Completion** | S4 | Retrospective—what worked, what didn't? |

When transitioning to S4 mode, pause and ask: "Are we still building the right thing, or should we adapt?"

---

## Responding to Algedonic Signals

For algedonic signal handling (HALT/ALERT responses, algedonic vs imPACT distinction), see [algedonic.md](../protocols/algedonic.md).

---

## Output Conciseness

**Default: Concise output.** The orchestrator's internal reasoning (variety analysis, dependency checking, execution strategy) runs internally. User sees only decisions and key context.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Variety dimension scores, full tables | One-line summary: `Variety: Low (5) — proceeding with orchestrate` |
| QDCL checklist, dependency analysis | Decision only: `Invoking 2 backend coders in parallel` |
| Phase skip reasoning details | Brief: `Skipping PREPARE/ARCHITECT (approved plan exists)` |

**User can always ask** for details (e.g., "Why that strategy?" or "Show me the variety analysis").

**Narration style**: State decisions, not reasoning process. Minimize commentary.

**Exceptions warranting more detail**:
- Error conditions, blockers, or unexpected issues — proactively explain what went wrong
- High-variety tasks (11+) — visible reasoning helps user track complex orchestration

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me assess variety and check for the approved plan" | (just do it, show result) |
| "I'm now going to invoke the backend coder" | `Invoking backend coder` |
| "S4 Mode — Task Assessment" | (implicit, don't announce) |

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
- **Novelty**: Routine (1) → Unprecedented (4)
- **Scope**: Single concern (1) → Cross-cutting (4)
- **Uncertainty**: Clear (1) → Unknown (4)
- **Risk**: Low impact (1) → Critical (4)

**Output format**: One-line summary only. Example: `Variety: Medium (8) — standard orchestrate with all phases`

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

---

## Execution Philosophy

**MANDATORY: Invoke concurrently unless blocked.** The burden of proof is on sequential dispatch. If you cannot cite a specific file conflict or data dependency, you MUST invoke them concurrently.

This applies across ALL phases, not just CODE:
- PREPARE with multiple research areas → multiple preparers at once
- ARCHITECT with independent component designs → multiple architects simultaneously
- CODE with multiple domains or independent tasks → multiple coders together
- TEST with independent test suites → multiple test engineers concurrently

Sequential execution is the exception requiring explicit justification. When assessing any phase, ask: "Can specialists be invoked concurrently?" The answer is usually yes.

---

1. **Set up worktree**: If already in a worktree for this feature, reuse it. Otherwise, invoke `/PACT:worktree-setup` with the feature branch name. This creates both the feature branch and its worktree. All subsequent phases work in the worktree.
2. **Create team**: If no team exists for this branch: `TeamCreate(team_name="pact-{branch-name}")`. The team persists for the session — all specialists join this team.
3. **Check for plan** in `docs/plans/` matching this task

### Plan Status Handling

| Status | Action |
|--------|--------|
| PENDING APPROVAL | `/PACT:orchestrate` = implicit approval → update to IN_PROGRESS |
| APPROVED | Update to IN_PROGRESS |
| BLOCKED | Ask user to resolve or proceed without plan |
| IN_PROGRESS | Confirm: continue or restart? |
| SUPERSEDED/IMPLEMENTED | Confirm with user before proceeding |
| No plan found | Proceed—phases will do full discovery |

### Phase Transitions

Lead monitors for phase completion via `SendMessage` from teammates (HANDOFF messages) and `TaskList` status. When all phase tasks are completed, create next phase's tasks and spawn next phase's teammates. Previous-phase teammates remain as consultants.

---

## Context Assessment

Before executing phases, assess which are needed based on existing context:

| Phase | Run if... | Skip if... |
|-------|-----------|------------|
| **PREPARE** | Requirements unclear, external APIs to research, dependencies unmapped | Approved plan with complete Preparation section (passes completeness check below); OR requirements explicit in task; OR existing `docs/preparation/` covers scope with no unresolved items |
| **ARCHITECT** | New component or module, interface contracts undefined, architectural decisions required | Approved plan with complete Architecture section (passes completeness check below); OR following established patterns with no new components; OR `docs/architecture/` covers design with no open items |
| **CODE** | Always run | Never skip |
| **TEST** | Integration/E2E tests needed, complex component interactions, security/performance verification | ALL of the following are true: (1) trivial change with no new logic requiring tests, (2) no integration boundaries crossed, (3) isolated change with no meaningful test scenarios, AND (4) plan's Phase Requirements section does not mark TEST as REQUIRED (if plan exists) |

**Conflict resolution**: When both "Run if" and "Skip if" criteria apply, **run the phase** (safer default). Example: A plan exists but requirements have changed—run PREPARE to validate.

**Plan-aware fast path**: When an approved plan exists in `docs/plans/`, PREPARE and ARCHITECT are typically skippable—the plan already synthesized specialist perspectives. Skip unless scope has changed, plan appears stale (typically >2 weeks; ask user to confirm if uncertain), OR the plan contains incompleteness signals for that phase (see Phase Skip Completeness Check below).

**State your assessment briefly.** Example: `Skipping PREPARE/ARCHITECT (approved plan exists). Running CODE and TEST.`

The user can override your assessment or ask for details.

### Phase Skip Completeness Check

**Principle: Existence ≠ Completeness.**

Before skipping, scan the plan section for incompleteness signals (see [pact-completeness.md](../protocols/pact-completeness.md)):
- [ ] No unchecked research items (`- [ ]`)
- [ ] No TBD values in decision tables
- [ ] No `⚠️ Handled during {PHASE_NAME}` forward references
- [ ] No unchecked questions to resolve
- [ ] No empty/placeholder sections
- [ ] No unresolved open questions

**All clear** → Skip with reason `"plan_section_complete"` (not `"approved_plan_exists"`)
**Any signal present** → Run the phase

> **Note**: The plan's Phase Requirements table is advisory. When in doubt, verify against actual section content — the table may be stale if the plan was updated after initial synthesis.

**Scope detection**: After PREPARE completes (or is skipped), scope detection evaluates whether the task warrants decomposition into sub-scopes. See [Scope Detection Evaluation](#scope-detection-evaluation) below.

---

## Handling Decisions When Phases Were Skipped

When a phase is skipped but a coder encounters a decision that would have been handled by that phase:

| Decision Scope | Examples | Action |
|----------------|----------|--------|
| **Minor** | Naming conventions, local file structure, error message wording | Coder decides, documents in commit message |
| **Moderate** | Interface shape within your module, error handling pattern, internal component boundaries | Coder decides and implements, but flags decision with rationale in handoff; orchestrator validates before next phase |
| **Major** | New module needed, cross-module contract, architectural pattern affecting multiple components | Blocker → `/PACT:imPACT` → may need to run skipped phase |

**Boundary heuristic**: If a decision affects files outside the current specialist's scope, treat it as Major.

**Coder instruction when phases were skipped**:

> "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the handoff so it can be validated. Major decisions affecting other components are blockers—don't implement, escalate."

---

### PREPARE Phase → `pact-preparer`

**Skip criteria met (including completeness check)?** → Proceed to ARCHITECT phase.

**Plan sections to pass** (if plan exists):
- "Preparation Phase"
- "Open Questions > Require Further Research"

**Dispatch `pact-preparer`**:
1. `TaskCreate(subject="preparer: research {feature}", description="CONTEXT: ...\nMISSION: ...\nINSTRUCTIONS: ...\nGUIDELINES: ...")`
   - Include task description, plan sections (if any), and "Reference the approved plan at `docs/plans/{slug}-plan.md` for full context."
2. `TaskUpdate(taskId, owner="preparer")`
3. `Task(name="preparer", team_name="pact-{branch}", subagent_type="pact-preparer", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")`

Completed-phase teammates remain as consultants. Do not shutdown during this workflow.

**Before next phase**:
- [ ] Outputs exist in `docs/preparation/`
- [ ] Specialist handoff received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **S4 Checkpoint** (see [pact-s4-checkpoints.md](../protocols/pact-s4-checkpoints.md)): Environment stable? Model aligned? Plan viable?

**Concurrent dispatch within PREPARE**: If research spans multiple independent areas (e.g., "research auth options AND caching strategies"), invoke multiple preparers together with clear scope boundaries.

---

### Post-PREPARE Re-assessment

If PREPARE ran and ARCHITECT was marked "Skip," compare PREPARE's recommended approach to the skip rationale:

- **Approach matches rationale** → Skip holds
- **Novel approach** (new components, interfaces, expanded scope) → Override, run ARCHITECT

**Example**:
> Skip rationale: "following established pattern in `src/utils/`"
> PREPARE recommends "add helper to existing utils" → Skip holds
> PREPARE recommends "new ValidationService class" → Override, run ARCHITECT

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
| Score below threshold | Single scope — continue with today's behavior |
| Score at/above threshold | Propose decomposition (see Evaluation Response below) |
| All strong signals fire, no counter-signals, autonomous enabled | Auto-decompose (see Evaluation Response below) |

**Output format**: `Scope detection: Single scope (score 2/3 threshold)` or `Scope detection: Multi-scope detected (score 4/3 threshold) — proposing decomposition`

#### Evaluation Response

When detection fires (score >= threshold), follow the evaluation response protocol in [pact-scope-detection.md](../protocols/pact-scope-detection.md) — S5 confirmation flow, user response mapping, and autonomous tier.

**On confirmed decomposition**: Generate a scope contract for each sub-scope before invoking rePACT. See [pact-scope-contract.md](../protocols/pact-scope-contract.md) for the contract format and generation process. Skip top-level ARCHITECT and CODE — mark both `completed` with `{"skipped": true, "skip_reason": "decomposition_active"}`. The workflow switches to scoped PACT phases: ATOMIZE (dispatch sub-scopes) → CONSOLIDATE (verify contracts) → TEST (comprehensive feature testing). See ATOMIZE Phase and CONSOLIDATE Phase below.

---

### ARCHITECT Phase → `pact-architect`

**Skip criteria met (including completeness check, after re-assessment)?** → Proceed to CODE phase.

**Plan sections to pass** (if plan exists):
- "Architecture Phase"
- "Key Decisions"
- "Interface Contracts"

**Dispatch `pact-architect`**:
1. `TaskCreate(subject="architect: design {feature}", description="CONTEXT: ...\nMISSION: ...\nINSTRUCTIONS: ...\nGUIDELINES: ...")`
   - Include task description, where to find PREPARE outputs (e.g., "Read `docs/preparation/{feature}.md`"), plan sections (if any), and plan reference.
   - Do not read phase output files yourself or paste their content into the task description.
   - If PREPARE was skipped: pass the plan's Preparation Phase section instead.
2. `TaskUpdate(taskId, owner="architect")`
3. `Task(name="architect", team_name="pact-{branch}", subagent_type="pact-architect", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")`

Completed-phase teammates remain as consultants. Do not shutdown during this workflow.

**Before next phase**:
- [ ] Outputs exist in `docs/architecture/`
- [ ] Specialist handoff received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **S4 Checkpoint**: Environment stable? Model aligned? Plan viable?

**Concurrent dispatch within ARCHITECT**: If designing multiple independent components (e.g., "design user service AND notification service"), invoke multiple architects simultaneously. Ensure interface contracts between components are defined as a coordination checkpoint.

---

### CODE Phase → `pact-*-coder(s)`

**Always runs.** This is the core work.

> **S5 Policy Checkpoint (Pre-CODE)**: Before invoking coders, verify:
> 1. "Does the architecture align with project principles?"
> 2. "Am I delegating ALL code changes to specialists?" (orchestrator writes no application code)
> 3. "Are there any S5 non-negotiables at risk?"
>
> **Delegation reminder**: Even if you identified the exact implementation during earlier phases, you must delegate the actual coding. Knowing what to build ≠ permission to build it yourself.

**Plan sections to pass** (if plan exists):
- "Code Phase"
- "Implementation Sequence"
- "Commit Sequence"

**Select coder(s)** based on scope:
- `pact-backend-coder` — server-side logic, APIs
- `pact-frontend-coder` — UI, client-side
- `pact-database-engineer` — schema, queries, migrations

#### Invoke Concurrently by Default

**Default stance**: Dispatch specialists together unless proven dependent. Sequential requires explicit justification.

**Required decision output** (no exceptions):
- "**Concurrent**: [groupings]" — the expected outcome
- "**Sequential because [specific reason]**: [ordering]" — requires explicit justification
- "**Mixed**: [concurrent groupings], then [sequential dependencies]" — when genuinely mixed

**Deviation from concurrent dispatch requires articulated reasoning.** "I'm not sure" defaults to concurrent with S2 coordination, not sequential.

**Analysis should complete quickly.** Use the Quick Dependency Checklist (QDCL) below. If QDCL analysis takes more than 2 minutes, you're likely over-analyzing independent tasks—default to concurrent dispatch with S2 coordination.

#### Execution Strategy Analysis

**REQUIRED**: Complete the QDCL internally before invoking coders.

**Quick Dependency Checklist (QDCL)** — run mentally, don't output:

For each pair of work units, check:
- Same file modified? → Sequential (or define strict boundaries)
- A's output is B's input? → Sequential (A first)
- Shared interface undefined? → Define interface first, then concurrent
- None of above? → Concurrent

**Output format**: Decision only. Example: `Invoking backend + frontend coders in parallel` or `Sequential: database first, then backend (schema dependency)`

**If QDCL shows no dependencies**: Concurrent is your answer. Don't second-guess.

#### S2 Pre-Dispatch Coordination

Before concurrent dispatch, check internally: shared files? shared interfaces? conventions established?

- **Shared files**: Sequence those agents OR assign clear boundaries
- **Conventions**: First agent's choice becomes standard; propagate to others
- **Resolution authority**: Technical disagreements → Architect arbitrates; Style/convention → First agent's choice

**Output**: Silent if no conflicts; only mention if conflicts found (e.g., `S2 check: types.ts shared — backend writes, frontend reads`).

**Include in prompts for concurrent specialists**: "You are working concurrently with other specialists. Your scope is [files]. Do not modify files outside your scope."

**Include worktree path in all agent prompts**: "You are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree."

**Dispatch coder(s)**:

For each coder needed:
1. `TaskCreate(subject="{coder-type}: implement {scope}", description="CONTEXT: ...\nMISSION: ...\nINSTRUCTIONS: ...\nGUIDELINES: ...")`
   - Include task description, where to find ARCHITECT outputs (e.g., "Read `docs/architecture/{feature}.md`"), plan sections (if any), plan reference.
   - Do not read phase output files yourself or paste their content into the task description.
   - If ARCHITECT was skipped: pass the plan's Architecture Phase section instead.
   - If PREPARE/ARCHITECT were skipped, include: "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the handoff so it can be validated. Major decisions affecting other components are blockers—don't implement, escalate."
   - Include: "Smoke Testing: Run the test suite before completing. If your changes break existing tests, fix them. Your tests are verification tests—enough to confirm your implementation works. Comprehensive coverage (edge cases, integration, E2E, adversarial) is TEST phase work."
2. `TaskUpdate(taskId, owner="{coder-name}")`
3. `Task(name="{coder-name}", team_name="pact-{branch}", subagent_type="pact-{coder-type}", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")`

Spawn multiple coders in parallel (multiple `Task` calls in one response). Include worktree path and S2 scope boundaries in each task description.

Completed-phase teammates remain as consultants. Do not shutdown during this workflow.

**Before next phase**:
- [ ] Implementation complete
- [ ] All tests passing (full test suite; fix any tests your changes break)
- [ ] Specialist handoff(s) received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of CODE phase work (preserves work before strategic re-assessment)
- [ ] **S4 Checkpoint**: Environment stable? Model aligned? Plan viable?

#### Handling Complex Sub-Tasks During CODE

If a sub-task emerges that is too complex for a single specialist invocation:

| Sub-Task Complexity | Indicators | Use |
|---------------------|------------|-----|
| **Simple** | Code-only, clear requirements | Direct specialist invocation |
| **Focused** | Single domain, no research needed | `/PACT:comPACT` |
| **Complex** | Needs own P→A→C→T cycle | `/PACT:rePACT` |

**When to use `/PACT:rePACT`:**
- Sub-task needs its own research/preparation phase
- Sub-task requires architectural decisions before coding
- Sub-task spans multiple concerns within a domain

**Phase re-entry** (via `/PACT:imPACT`): When imPACT decides to redo a prior phase, create a new retry phase task — do not reopen the completed one. See [imPACT.md Phase Re-Entry Task Protocol](imPACT.md#phase-re-entry-task-protocol) for details.

---

### ATOMIZE Phase (Scoped Orchestration Only)

Execute the [ATOMIZE Phase protocol](../protocols/pact-scope-phases.md#atomize-phase).

**Worktree isolation**: Before dispatching each sub-scope's rePACT, invoke `/PACT:worktree-setup` with the suffix branch name (e.g., `feature-X--backend`). Pass the resulting worktree path to the rePACT invocation.

---

### CONSOLIDATE Phase (Scoped Orchestration Only)

Execute the [CONSOLIDATE Phase protocol](../protocols/pact-scope-phases.md#consolidate-phase).

**Worktree cleanup**: After merging each sub-scope branch back to the feature branch, invoke `/PACT:worktree-cleanup` for that sub-scope's worktree.

---

### TEST Phase → `pact-test-engineer`

**Skip criteria met?** → Proceed to "After All Phases Complete."

**Plan sections to pass** (if plan exists):
- "Test Phase"
- "Test Scenarios"
- "Coverage Targets"

**Dispatch `pact-test-engineer`**:
1. `TaskCreate(subject="test-engineer: test {feature}", description="CONTEXT: ...\nMISSION: ...\nINSTRUCTIONS: ...\nGUIDELINES: ...")`
   - Include task description, CODE phase handoff summaries (from SendMessage, not files), plan sections (if any), plan reference.
   - Include: "You own ALL substantive testing: unit tests, integration, E2E, edge cases."
2. `TaskUpdate(taskId, owner="test-engineer")`
3. `Task(name="test-engineer", team_name="pact-{branch}", subagent_type="pact-test-engineer", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")`

**Before completing**:
- [ ] All tests passing
- [ ] Specialist handoff received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of TEST phase work (preserves work before strategic re-assessment)

**Concurrent dispatch within TEST**: If test suites are independent (e.g., "unit tests AND E2E tests" or "API tests AND UI tests"), invoke multiple test engineers at once with clear suite boundaries.

---

## Agent Stall Detection

For stall detection indicators, recovery protocol, prevention, and non-happy-path task termination, see [pact-agent-stall.md](../protocols/pact-agent-stall.md).

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **SendMessage**: Teammates send blockers and algedonic signals directly to the lead
- **TaskList**: Check for tasks with blocker metadata or stalled status
- After each agent dispatch, when agent reports completion, on any unexpected stoppage

On signal detected: Follow Signal Task Handling in CLAUDE.md.

---

## After All Phases Complete

> **S5 Policy Checkpoint (Pre-PR)**: Before creating PR, verify: "Do all tests pass? Is system integrity maintained? Have S5 non-negotiables been respected throughout?"

1. **Update plan status** (if plan exists): IN_PROGRESS → IMPLEMENTED
2. **Verify all work is committed** — CODE and TEST phase commits should already exist; if any uncommitted changes remain, commit them now
3. **TaskUpdate**: Feature task status = "completed" (all phases done, all work committed)

> ⚠️ **Keep coders alive through peer-review.** Peer review commonly surfaces issues requiring fixes. The original coders have the best context for remediation — do not shut them down before peer-review and all remediation complete. Shut down coders only after the user's merge decision.

4. **Run `/PACT:peer-review`** to create PR and get multi-agent review
5. **Present review summary and stop** — orchestrator never merges (S5 policy)
6. **S4 Retrospective** (after user decides): Briefly note—what worked well? What should we adapt for next time?
7. **High-variety audit trail** (variety 10+ only): Delegate to `pact-memory-agent` to save key orchestration decisions, S3/S4 tensions resolved, and lessons learned
