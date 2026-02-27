---
description: Delegate a task to PACT specialist agents
argument-hint: [e.g., implement feature X]
---
Orchestrate specialist PACT agents through the PACT workflow to address: $ARGUMENTS

---

## Task Hierarchy

Create the full Task hierarchy upfront for workflow visibility:

```
1. `TaskCreate`: Feature task "{verb} {feature}"
2. `TaskCreate`: Phase tasks (all upfront):
   - "PREPARE: {feature-slug}"
   - "ARCHITECT: {feature-slug}"
   - "CODE: {feature-slug}"
   - "TEST: {feature-slug}"
3. `TaskUpdate`: Set phase-to-phase blockedBy chain:
   - ARCHITECT blockedBy PREPARE
   - CODE blockedBy ARCHITECT
   - TEST blockedBy CODE
4. `TaskUpdate`: Feature task status = "in_progress"
```

**Scoped PACT phases**: When decomposition fires after PREPARE, the standard ARCHITECT and CODE phases are skipped (`decomposition_active`) and replaced by scoped phases. Create retroactively (detection occurs after PREPARE):
- `"ATOMIZE: {feature-slug}"` with `blockedBy = [PREPARE task ID]`
- `"CONSOLIDATE: {feature-slug}"` with `blockedBy = [all scope task IDs]`
- Add CONSOLIDATE to TEST's `blockedBy` via `addBlockedBy = [CONSOLIDATE task ID]` (the original CODE dependency auto-resolves when CODE is marked completed/skipped)

The scoped flow is: **P**repare → **A**tomize → **C**onsolidate → **T**est (same PACT acronym, scoped meanings).

For each phase execution:
```
a. `TaskUpdate`: phase status = "in_progress"
b. Analyze work needed (QDCL for CODE)
c. `TaskCreate`: agent task(s) as children of phase
d. `TaskUpdate`: agent tasks owner = "{agent-name}"
e. `TaskUpdate`: next phase addBlockedBy = [agent IDs]
f. Spawn teammates: Task(name="{name}", team_name="{team_name}", subagent_type="pact-{type}", prompt="...")
g. Store agent IDs: `TaskUpdate(taskId, metadata={"agent_id": "{id_from_Task_return}"})`
h. Monitor via `SendMessage` (completion summaries) and `TaskList` until agents complete
i. `TaskUpdate`: phase status = "completed" (agents self-manage their task status)
```

> **Why store agent_id?** Enables `resume` for blocker recovery — see [Blocker Recovery](#blocker-recovery-resume-vs-fresh-spawn).

**Skipped phases**: Mark directly `completed` (no `in_progress` — no work occurs):
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "{reason}"})`
Valid reasons: `"plan_section_complete"`, `"structured_gate_passed"`, `"decomposition_active"`.
<!-- Skip reason semantics:
  - "plan_section_complete": Plan exists AND section passed completeness check (Layer 2)
  - "structured_gate_passed": All structured analysis questions answered concretely (Layer 3)
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
2. **Verify session team exists**: The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.
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

Lead monitors for phase completion via `SendMessage` from teammates (completion summaries) and `TaskList` status. When all phase tasks are completed, create next phase's tasks and spawn next phase's teammates. Previous-phase teammates remain as consultants.

---

## Context Assessment: Phase Skip Decision Flow

Phases run by default. Skipping is an exception that must be earned through a structured gate. The posture is *"can I justify not running this?"* — not *"should I skip?"*

### Three Layers of Skip Protection

All three layers must pass before PREPARE or ARCHITECT can be skipped. Failure at any layer means the phase runs.

| Layer | What It Checks | Fail = |
|-------|----------------|--------|
| **1. Variety Hard Gates** | Dimension scores vs thresholds | Phase locked to "run" — no override possible |
| **2. Plan Completeness** | Approved plan + 7 incompleteness signals (see below) | Phase runs (plan incomplete or absent) |
| **3. Structured Analysis** | Concrete questions requiring specific, verifiable answers | Phase runs (analysis insufficient) |

Layer 1 is a numeric gate — no rationalization possible. Layer 2 is the existing completeness check (unchanged). Layer 3 replaces the old subjective skip criteria.

### Layer 1: Variety Hard Gates

Wire variety dimension scores (already computed in the Task Variety Assessment above) into skip eligibility as non-negotiable gates. These fire before any subjective analysis.

**PREPARE Hard Gates**:

| Dimension | Threshold | Rationale |
|-----------|-----------|-----------|
| **Novelty** ≥ 3 | Phase locked | Unfamiliar territory = unknown unknowns |
| **Uncertainty** ≥ 3 | Phase locked | Unclear requirements = research needed |

**ARCHITECT Hard Gates**:

| Dimension | Threshold | Rationale |
|-----------|-----------|-----------|
| **Scope** ≥ 3 | Phase locked | Cross-cutting change = architectural impact |
| **Risk** ≥ 3 | Phase locked | High stakes = design before coding |

**Global Gate**: If **total variety ≥ 10**, both PREPARE and ARCHITECT are locked to "run" regardless of individual dimensions.

**Threshold rationale**: Dimensions score 1-4. Score 2 = "somewhat familiar/clear" — not alarming. Score 3 = "mostly unfamiliar/unclear" — genuinely warrants the phase. Using ≥ 3 avoids false-locking on routine tasks.

**If any hard gate fires** → Phase runs. No further analysis needed for this phase.

### Layer 3: Structured Analysis Gate

When variety hard gates don't lock a phase and no approved plan covers the phase (or the plan section is incomplete), the orchestrator must answer concrete questions to earn a skip. Vague or hedged answers ("probably none", "I think so") mean the phase runs.

**PREPARE — Skip Analysis Questions** (must answer ALL three concretely):

1. **Adjacency check**: *"List all files beyond the direct target(s) that this change could affect."*
   - Must answer with specific file paths or confidently state "none — change is fully isolated to [files]."

2. **Dependency check**: *"What external dependencies, APIs, or constraints exist that aren't stated in the task description?"*
   - Must name them or state "none — all dependencies are documented in the task."

3. **Unknown-unknowns check**: *"What question could PREPARE answer that you can't answer right now?"*
   - Must state "none" with reasoning, not just assertion.

**ARCHITECT — Skip Analysis Questions** (must answer ALL three concretely):

1. **Interface check**: *"Does this change modify or create any interface contract (API, type, schema, protocol) consumed by other components?"*
   - Must answer yes/no with specifics.

2. **Pattern check**: *"What architectural pattern is being followed, and where is it established in the codebase?"*
   - Must cite a specific existing pattern with file references, not just assert "established patterns."

3. **Impact check**: *"Could a reasonable architect disagree with the approach implied by this task?"*
   - If yes, ARCHITECT runs. "No" requires brief reasoning.

**Key property**: These questions require **specific, verifiable answers** (file paths, pattern references, named dependencies) rather than subjective assertions.

**When skipping via structured analysis**: Record the analysis in task metadata for auditability:
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "structured_gate_passed", "skip_analysis": {"adjacency": "...", "dependency": "...", "unknown_unknowns": "..."}})`

### Per-Phase Decision Flow

For each of PREPARE and ARCHITECT, evaluate in order:

```
1. Check Layer 1 (variety hard gates) → Locked? → Phase RUNS (no further analysis)
2. Check approved plan + Layer 2 (completeness check) → Plan section complete? → SKIP (reason: "plan_section_complete")
3. Run Layer 3 (structured analysis gate) → All questions answered concretely? → SKIP (reason: "structured_gate_passed")
4. Default → Phase RUNS
```

**CODE phase**: Always runs. Never skip.

**TEST phase**: Skip criteria unchanged — requires ALL four conditions: (1) trivial change with no new logic, (2) no integration boundaries crossed, (3) isolated with no meaningful test scenarios, AND (4) plan doesn't mark TEST as REQUIRED.

**Conflict resolution**: When analysis is ambiguous, phase runs. The burden of proof is on skipping.

**State your assessment briefly.** Example: `Skipping PREPARE (structured gate passed). Running ARCHITECT, CODE, TEST.`

The user can override your assessment or ask for details (e.g., "Show me the skip analysis").

### Phase Skip Completeness Check (Layer 2)

**Principle: Existence ≠ Completeness.** This is Layer 2 of the skip protection (see Per-Phase Decision Flow above). It applies when skipping based on an approved plan.

Before skipping, scan the plan section for incompleteness signals (see [pact-completeness.md](../protocols/pact-completeness.md)):
- [ ] No unchecked research items (`- [ ]`)
- [ ] No TBD values in decision tables
- [ ] No `⚠️ Handled during {PHASE_NAME}` forward references
- [ ] No unchecked questions to resolve
- [ ] No empty/placeholder sections
- [ ] No unresolved open questions
- [ ] No research/investigation tasks in implementation plan (go/no-go items, feasibility studies, audit tasks)

**All clear** → Skip with reason `"plan_section_complete"`
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

**Phase skip decision flow passed (all 3 layers)?** → Mark PREPARE `completed` with skip metadata and proceed to ARCHITECT phase.

**Plan sections to pass** (if plan exists):
- "Preparation Phase"
- "Open Questions > Require Further Research"

**Dispatch `pact-preparer`**:
1. `TaskCreate(subject="preparer: research {feature}", description="CONTEXT: ...\nMISSION: ...\nINSTRUCTIONS: ...\nGUIDELINES: ...")`
   - Include task description, plan sections (if any), and "Reference the approved plan at `docs/plans/{slug}-plan.md` for full context."
2. `TaskUpdate(taskId, owner="preparer")`
3. `Task(name="preparer", team_name="{team_name}", subagent_type="pact-preparer", prompt="You are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`

Completed-phase teammates remain as consultants. Do not shutdown during this workflow.

**Before next phase**:
- [ ] Outputs exist in `docs/preparation/`
- [ ] Specialist handoff received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **S4 Checkpoint** (see [pact-s4-checkpoints.md](../protocols/pact-s4-checkpoints.md)): Environment stable? Model aligned? Plan viable?

**Concurrent dispatch within PREPARE**: If research spans multiple independent areas (e.g., "research auth options AND caching strategies"), invoke multiple preparers together with clear scope boundaries.

---

### PREPARE→ARCHITECT Coupling

When PREPARE runs, the orchestrator reviews PREPARE output before evaluating ARCHITECT's skip eligibility:

- **PREPARE ran + ARCHITECT hard gates fire** (Scope ≥ 3 or Risk ≥ 3 or total ≥ 10) → Full ARCHITECT, no further analysis needed
- **PREPARE ran + ARCHITECT hard gates don't fire** → Review PREPARE output: *"Did PREPARE reveal new components, interface changes, pattern decisions, or cross-module impact?"* If yes → full ARCHITECT. If no → proceed to structured analysis gate for ARCHITECT.
- **PREPARE skipped** → ARCHITECT evaluated independently through its own gate (no coupling)

**Deferred skip decisions**: When PREPARE runs, do not pre-commit ARCHITECT's skip decision during Context Assessment. Defer until PREPARE results are available, then apply the coupling rules above.

The outcome is binary: full ARCHITECT or skip. No "light ARCHITECT" execution mode.

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

**Phase skip decision flow passed (all 3 layers, after PREPARE→ARCHITECT coupling if PREPARE ran)?** → Mark ARCHITECT `completed` with skip metadata and proceed to CODE phase.

**Plan sections to pass** (if plan exists):
- "Architecture Phase"
- "Key Decisions"
- "Interface Contracts"

**Dispatch `pact-architect`**:
1. `TaskCreate(subject="architect: design {feature}", description="CONTEXT: ...\nMISSION: ...\nINSTRUCTIONS: ...\nGUIDELINES: ...")`
   - Include task description, where to find PREPARE outputs (e.g., "Read `docs/preparation/{feature}.md`"), plan sections (if any), and plan reference.
   - Include upstream task reference: "Preparer task: #{taskId} — read via `TaskGet` for research decisions and context."
   - Do not read phase output files yourself or paste their content into the task description.
   - If PREPARE was skipped: pass the plan's Preparation Phase section instead.
2. `TaskUpdate(taskId, owner="architect")`
3. `Task(name="architect", team_name="{team_name}", subagent_type="pact-architect", prompt="You are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`

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
- `pact-devops-engineer` — CI/CD, Docker, infrastructure, build systems

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
   - Include upstream task references: "Architect task: #{taskId} — read via `TaskGet` for design decisions." If multiple coders are dispatched concurrently, include peer names: "Your peers on this phase: {other-coder-names}."
   - Do not read phase output files yourself or paste their content into the task description.
   - If ARCHITECT was skipped: pass the plan's Architecture Phase section instead.
   - If PREPARE/ARCHITECT were skipped, include: "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the handoff so it can be validated. Major decisions affecting other components are blockers—don't implement, escalate."
   - Include: "Smoke Testing: Run the test suite before completing. If your changes break existing tests, fix them. Your tests are verification tests—enough to confirm your implementation works. Comprehensive coverage (edge cases, integration, E2E, adversarial) is TEST phase work."
2. `TaskUpdate(taskId, owner="{coder-name}")`
3. `Task(name="{coder-name}", team_name="{team_name}", subagent_type="pact-{coder-type}", prompt="You are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`

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
   - Include task description, coder task references (e.g., "Coder tasks: #{id1}, #{id2} — read via `TaskGet` for implementation decisions and flagged uncertainties"), plan sections (if any), plan reference.
   - Include: "You own ALL substantive testing: unit tests, integration, E2E, edge cases."
2. `TaskUpdate(taskId, owner="test-engineer")`
3. `Task(name="test-engineer", team_name="{team_name}", subagent_type="pact-test-engineer", prompt="You are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`

**Before completing**:
- [ ] All tests passing
- [ ] Specialist handoff received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **Agreement verification (L2)**: Before creating PR, verify implementation fulfills original purpose. `SendMessage` to test engineer to verify: "Does the tested implementation match the original requirements?" Background: [pact-ct-teachback.md](../protocols/pact-ct-teachback.md).
- [ ] **Create atomic commit(s)** of TEST phase work (preserves work before strategic re-assessment)

**Concurrent dispatch within TEST**: If test suites are independent (e.g., "unit tests AND E2E tests" or "API tests AND UI tests"), invoke multiple test engineers at once with clear suite boundaries.

---

## Agent Stall Detection

For stall detection indicators, recovery protocol, prevention, and non-happy-path task termination, see [pact-agent-stall.md](../protocols/pact-agent-stall.md).

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **`SendMessage`**: Teammates send blockers and algedonic signals directly to the lead
- **`TaskList`**: Check for tasks with blocker metadata or stalled status
- After each agent dispatch, when agent reports completion, on any unexpected stoppage

On signal detected: Follow Signal Task Handling in CLAUDE.md.

**HALT handling**: On HALT signal, immediately `SendMessage(type="broadcast", content="[lead→all] ⚠️ HALT: {category}. Stop all work immediately. Preserve current state and await further instructions.", summary="HALT: {category}")` to stop all running teammates before presenting to user.

### Blocker Recovery: Resume vs. Fresh Spawn

When a blocker is resolved, prefer resuming the original agent over spawning fresh — this preserves the agent's accumulated context.

**Decision matrix**:

| Situation | Action | Rationale |
|-----------|--------|-----------|
| Blocker resolved, agent had significant partial work | `resume` | Preserve context |
| Blocker resolved, agent's approach was wrong | Fresh spawn | Clean slate needed |
| Agent hit `maxTurns` limit | Fresh spawn | Agent was likely looping |
| Agent shut down for lifecycle cleanup | Fresh spawn | Context is stale |

**Resume pattern**:
1. Read agent ID from task metadata: `TaskGet(taskId).metadata.agent_id`
2. Resume with blocker context: `Task(resume="{agent_id}", prompt="Blocker resolved: {details}. Continue your task.")`

**Fresh spawn pattern** (when resume is inappropriate): Follow the standard dispatch pattern (`TaskCreate` + `TaskUpdate` + Task with name/team_name/subagent_type).

---

## After All Phases Complete

> **S5 Policy Checkpoint (Pre-PR)**: Before creating PR, verify: "Do all tests pass? Is system integrity maintained? Have S5 non-negotiables been respected throughout?"

1. **Update plan status** (if plan exists): IN_PROGRESS → IMPLEMENTED
2. **Verify all work is committed** — CODE and TEST phase commits should already exist; if any uncommitted changes remain, commit them now
3. **`TaskUpdate`**: Feature task status = "completed" (all phases done, all work committed)
4. **Run `/PACT:peer-review`** to create PR and get multi-agent review
5. **Present review summary and stop** — use `AskUserQuestion` for merge authorization (S5 policy)
6. **S4 Retrospective** (after user decides): Briefly note—what worked well? What should we adapt for next time?
7. **High-variety audit trail** (variety 10+ only): Delegate to `pact-memory-agent` to save key orchestration decisions, S3/S4 tensions resolved, and lessons learned
