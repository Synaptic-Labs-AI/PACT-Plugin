---
description: Delegate within a single domain—concurrent agents for independent sub-tasks
argument-hint: [backend|frontend|database|prepare|test|architect] <task>
---
Delegate this focused task within a single PACT domain: $ARGUMENTS

**MANDATORY: invoke concurrently for independent sub-tasks.** Sequential requires explicit file conflict or data dependency. If the task contains multiple independent items (bugs, endpoints, components), dispatch multiple specialists of the same type together unless they share files.

> ⚠️ **Single domain ≠ single agent.** "Backend domain" with 3 bugs = 3 backend-coders in parallel. The domain is singular; the agents are not.

---

## Task Hierarchy

Create a simpler Task hierarchy than full orchestrate:

```
1. TaskCreate: Feature task "{verb} {feature}" (single-domain work)
2. TaskUpdate: Feature task status = "in_progress"
3. Analyze: How many agents needed?
4. TaskCreate: Agent task(s) — direct children of feature
5. TaskUpdate: Agent tasks status = "in_progress"
6. TaskUpdate: Feature task addBlockedBy = [all agent IDs]
7. Dispatch agents concurrently with task IDs
8. Monitor via TaskList until all agents complete
9. TaskUpdate: Agent tasks status = "completed" (as each completes)
10. TaskUpdate: Feature task status = "completed"
```

> Steps 8-10 are detailed in the [After Specialist Completes](#after-specialist-completes) section below (includes test verification and commit steps).

**Example structure:**
```
[Feature] "Fix 3 backend bugs"           (blockedBy: agent1, agent2, agent3)
├── [Agent] "backend-coder: fix bug A"
├── [Agent] "backend-coder: fix bug B"
└── [Agent] "backend-coder: fix bug C"
```

---

## Specialist Selection

| Shorthand | Specialist | Use For |
|-----------|------------|---------|
| `backend` | pact-backend-coder | Server-side logic, APIs, middleware |
| `frontend` | pact-frontend-coder | UI, React, client-side |
| `database` | pact-database-engineer | Schema, queries, migrations |
| `prepare` | pact-preparer | Research, requirements gathering |
| `test` | pact-test-engineer | Standalone test tasks |
| `architect` | pact-architect | Design guidance, pattern selection |

### If specialist not specified or unrecognized

If the first word isn't a recognized shorthand, treat the entire argument as the task and apply smart selection below.

**Auto-select when clear**:
- Task contains domain-specific keywords:
  - Frontend: React, Vue, UI, CSS, component
  - Backend: Express, API, endpoint, middleware, server
  - Database: PostgreSQL, MySQL, SQL, schema, migration, index
  - Test: Jest, test, spec, coverage
  - Prepare: research, investigate, requirements, explore, compare
  - Architect: pattern, singleton, factory, structure, architecture
- Task mentions specific file types (.tsx, .jsx, .sql, .spec.ts, etc.)
- Proceed immediately: "Delegating to [specialist]..."

**Ask when ambiguous**:
- Generic verbs without domain context (fix, improve, update)
- Feature-level scope that spans domains (login, user profile, dashboard)
- Performance/optimization without specific layer
- → Use `AskUserQuestion` tool:
  - Question: "Which specialist should handle this task?"
  - Options: List the 2-3 most likely specialists based on context (e.g., "Backend" / "Frontend" / "Database")

---

## When to Invoke Multiple Specialists

**MANDATORY: invoke concurrently unless tasks share files.** The burden of proof is on sequential dispatch.

Invoke concurrently when:
- Multiple independent items (bugs, components, endpoints)
- No shared files between sub-tasks
- Same patterns/conventions apply to all

**Examples:**
| Task | Agents Invoked |
|------|----------------|
| "Fix 3 backend bugs" | 3 backend-coders at once |
| "Add validation to 5 endpoints" | Multiple backend-coders simultaneously |
| "Update styling on 3 components" | Multiple frontend-coders together |

**Do NOT invoke concurrently when:**
- Sub-tasks modify the same files
- Sub-tasks have dependencies on each other
- Conventions haven't been established yet (run one first to set patterns, then dispatch the rest together)

---

## S2 Light Coordination (Required Before Concurrent Dispatch)

Before invoking multiple specialists concurrently, perform this coordination check:

1. **Identify potential conflicts**
   - List files each sub-task will touch
   - Flag any overlapping files

2. **Resolve conflicts (if any)**
   - **Same file**: Sequence those sub-tasks OR assign clear section boundaries
   - **Style/convention**: First agent's choice becomes standard

3. **Set boundaries**
   - Clearly state which sub-task handles which files/components
   - Include this in each specialist's prompt

**If conflicts cannot be resolved**: Sequence the work instead of dispatching concurrently.

---

## Output Conciseness

**Default: Concise output.** User sees delegation decisions, not coordination analysis.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| S2 coordination analysis, conflict checking | `Delegating to backend coder` |
| Concurrency reasoning, file boundary decisions | `Invoking 3 frontend coders in parallel` |
| Specialist selection logic | `Auto-selected: database (SQL keywords detected)` |

**User can always ask** for details (e.g., "Why that specialist?" or "Show me the conflict analysis").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me check if these sub-tasks share files..." | (just do it, report result) |
| "I'm analyzing whether to invoke concurrently..." | `Concurrent: no shared files` |

---

## Pre-Invocation (Required)

1. **Set up worktree** — If already in a worktree for this feature, reuse it. Otherwise, invoke `/PACT:worktree-setup` with the feature branch name. All subsequent work happens in the worktree.
2. **S2 coordination** (if concurrent) — Check for file conflicts, assign boundaries

---

## Invocation

### Multiple Specialists Concurrently (Default)

When the task contains multiple independent items, invoke multiple specialists together with boundary context:

```
comPACT mode (concurrent): You are one of [N] specialists working concurrently.
You are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree.

YOUR SCOPE: [specific sub-task and files this agent owns]
OTHER AGENTS' SCOPE: [what other agents are handling - do not touch]

Work directly from this task description.
Check docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist—reference relevant context.
Do not create new documentation artifacts in docs/.
Stay within your assigned scope—do not modify files outside your boundary.

Testing responsibilities:
- New unit tests: Required for logic changes.
- Existing tests: If your changes break existing tests, fix them.
- Before handoff: Run the test suite for your scope.

If you hit a blocker or need to modify files outside your scope, STOP and report it.

Task: [this agent's specific sub-task]
```

**After all concurrent agents complete**: Verify no conflicts occurred, run full test suite.

### Single Specialist Agent (When Required)

Use a single specialist agent only when:
- Task is atomic (one bug, one endpoint, one component)
- Sub-tasks modify the same files
- Sub-tasks have dependencies on each other
- Conventions haven't been established yet (run one first to set patterns)

**Invoke the specialist with**:
```
comPACT mode: Work directly from this task description.
You are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree.
Check docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist—reference relevant context.
Do not create new documentation artifacts in docs/.
Focus on the task at hand.
Testing responsibilities:
- New unit tests: Required for logic changes; optional for trivial changes (documentation, comments, config).
- Existing tests: If your changes break existing tests, fix them.
- Before handoff: Run the test suite and ensure all tests pass.

> **Smoke vs comprehensive tests**: These are verification tests—enough to confirm your implementation works. Comprehensive coverage (edge cases, integration, E2E, adversarial) is TEST phase work handled by `pact-test-engineer`.

If you hit a blocker, STOP and report it so the orchestrator can run /PACT:imPACT.

Task: [user's task description]
```

---

## Signal Monitoring

Check TaskList for blocker/algedonic signals:
- After each agent dispatch
- When agent reports completion
- On any unexpected agent stoppage

On signal detected: Follow Signal Task Handling in CLAUDE.md.

For agent stall detection and recovery, see [Agent Stall Detection](orchestrate.md#agent-stall-detection).

---

## After Specialist Completes

1. **Receive handoff** from specialist(s)
2. **TaskUpdate**: Agent tasks status = "completed" (as each completes)
3. **Run tests** — verify work passes. If tests fail → return to specialist for fixes (create new agent task, repeat from step 1).
4. **Create atomic commit(s)** — stage and commit before proceeding
5. **TaskUpdate**: Feature task status = "completed"

**Next steps** — After commit, ask: "Work committed. Create PR?"
- **Yes (Recommended)** → invoke `/PACT:peer-review`
- **Not yet** → worktree persists; user resumes later. Clean up manually with `/PACT:worktree-cleanup` when done.
- **More work** → continue with comPACT or orchestrate

**If blocker reported**:

Examples of blockers:
- Task requires a different specialist's domain
- Missing dependencies, access, or information
- Same error persists after multiple fix attempts
- Scope exceeds single-domain capability (needs cross-domain coordination)
- Concurrent agents have unresolvable conflicts

When blocker is reported:
1. Receive blocker report from specialist
2. Run `/PACT:imPACT` to triage
3. May escalate to `/PACT:orchestrate` if task exceeds single-domain scope

---

## When to Escalate

Recommend `/PACT:orchestrate` instead if:
- Task spans multiple specialist domains
- Complex cross-domain coordination needed
- Architectural decisions affect multiple components
- Full preparation/architecture documentation is needed

### Variety-Aware Escalation

During comPACT execution, if you discover the task is more complex than expected:

| Discovery | Variety Signal | Action |
|-----------|----------------|--------|
| Task spans multiple domains | Medium+ (7+) | Escalate to `/PACT:orchestrate` |
| Significant ambiguity/uncertainty | High (11+) | Escalate; may need PREPARE phase |
| Architectural decisions required | High (11+) | Escalate; need ARCHITECT phase |
| Higher risk than expected | High (11+) | Consider `/PACT:plan-mode` first |

**Heuristic**: If re-assessing variety would now score Medium+ (7+), escalate.

**Conversely**, if the specialist reports the task is simpler than expected:
- Note in handoff to orchestrator
- Complete the task; orchestrator may simplify remaining work
