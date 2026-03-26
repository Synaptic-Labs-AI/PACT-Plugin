---
description: Dispatch concurrent specialists for self-contained tasks. No PACT phases needed.
argument-hint: [backend|frontend|database|prepare|test|architect|devops|security|qa] <task>
---
Dispatch concurrent specialists for this self-contained task: $ARGUMENTS

**MANDATORY: invoke concurrently for independent sub-tasks.** Sequential requires explicit file conflict or data dependency. If the task contains multiple independent items (bugs, endpoints, components), dispatch multiple specialists together — same type or mixed types — unless they share files.

> ⚠️ **Independent ≠ same domain.** "Fix CSS layout + add server logging" = 1 frontend-coder + 1 backend-coder in parallel. The key criterion is independence (no shared files, no data dependencies), not domain uniformity.

---

## Task Hierarchy

Create a simpler Task hierarchy than full orchestrate:

```
1. `TaskCreate`: Feature task "{verb} {feature}" (self-contained task)
2. `TaskUpdate`: Feature task status = "in_progress"
3. Analyze: How many agents needed?
4. `TaskCreate`: Agent task(s) — direct children of feature
5. `TaskUpdate`: Agent tasks status = "in_progress"
6. `TaskUpdate`: Feature task addBlockedBy = [all agent IDs]
7. Dispatch agents concurrently with task IDs
8. Monitor via `TaskList` until all agents complete
9. `TaskUpdate`: Agent tasks status = "completed" (as each completes)
10. `TaskUpdate`: Feature task status = "completed"
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
| `devops` | pact-devops-engineer | CI/CD, Docker, scripts, infrastructure |
| `security` | pact-security-engineer | Security audit of existing code |
| `qa` | pact-qa-engineer | Runtime verification of app behavior |

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
  - DevOps: CI/CD, Docker, Dockerfile, pipeline, deploy, infrastructure, Terraform, Makefile, GitHub Actions, workflow, container, Pulumi, CloudFormation
  - Security: vulnerability, CVE, injection, XSS, auth bypass, security audit, penetration, OWASP, secrets, credential
  - QA: runtime, exploratory, browser, Playwright, visual, smoke test, manual test, visual regression, user flow
- Task mentions specific file types (.tsx, .jsx, .sql, .spec.ts, .yml, .yaml, Dockerfile, .sh, .tf, .toml, etc.)
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
| "Add API endpoint + update DB index" | 1 backend-coder + 1 database-engineer (parallel) |
| "Fix CSS layout + add server logging" | 1 frontend-coder + 1 backend-coder (parallel) |

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

4. **Environment drift** — When dispatching subsequent agents after earlier ones complete, check `file-edits.json` for modified files and include deltas in prompts (see [pact-s2-coordination.md](../protocols/pact-s2-coordination.md#environment-drift-detection))

5. **Persist `s2_boundaries` and `established_conventions`** — `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {...}, "established_conventions": {...}})`

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
2. **Verify session team exists** — The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.
3. **S2 coordination** (if concurrent) — Check for file conflicts, assign boundaries

> **Teachback**: All dispatched specialists send a teachback before starting work (see [pact-ct-teachback.md](../protocols/pact-ct-teachback.md)).

---

## Invocation

### Multiple Specialists Concurrently (Default)

When the task contains multiple independent items, invoke multiple specialists together with boundary context:

For each specialist needed:
1. `TaskCreate(subject="{specialist}: {sub-task}", description="comPACT mode (concurrent): You are one of [N] specialists working concurrently.\nYou are working in a git worktree at [worktree_path].\nNote: `CLAUDE.md` is gitignored and does not exist in worktrees. Do NOT edit or create `CLAUDE.md` — the orchestrator manages it separately. If your task mentions updating `CLAUDE.md`, flag it in your handoff instead.\n\nYOUR SCOPE: [specific sub-task]\nOTHER AGENTS' SCOPE: [what others handle]\n\nWork directly from this task description.\nIf upstream task IDs are provided, read via `TaskGet` for prior decisions.\nCheck docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist.\nDo not create new documentation artifacts in docs/.\nStay within your assigned scope.\n\nTesting: New unit tests for logic changes. Fix broken existing tests. Run test suite before handoff.\n\nIf you hit a blocker, STOP and `SendMessage` it to the lead.\n\nTask: [this agent's specific sub-task]")`
2. `TaskUpdate(taskId, owner="{specialist-name}")`
3. `Task(name="{specialist-name}", team_name="{team_name}", subagent_type="pact-{specialist-type}", prompt="You are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`

Spawn all specialists in parallel (multiple `Task` calls in one response).

**Progress monitoring**: For parallel dispatch or novel domains, include "Send progress signals per the agent-teams skill Progress Signals section" in each specialist's dispatch prompt.

**After all concurrent agents complete**: Verify no conflicts occurred, run full test suite.

### Single Specialist Agent (When Required)

Use a single specialist agent only when:
- Task is atomic (one bug, one endpoint, one component)
- Sub-tasks modify the same files
- Sub-tasks have dependencies on each other
- Conventions haven't been established yet (run one first to set patterns)

**Dispatch the specialist**:
1. `TaskCreate(subject="{specialist}: {task}", description="comPACT mode: Work directly from this task description.\nYou are working in a git worktree at [worktree_path].\nNote: `CLAUDE.md` is gitignored and does not exist in worktrees. Do NOT edit or create `CLAUDE.md` — the orchestrator manages it separately. If your task mentions updating `CLAUDE.md`, flag it in your handoff instead.\nIf upstream task IDs are provided, read via `TaskGet` for prior decisions.\nCheck docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist.\nDo not create new documentation artifacts in docs/.\nFocus on the task at hand.\n\nTesting: New unit tests for logic changes (optional for trivial changes). Fix broken existing tests. Run test suite before handoff.\n\n> Smoke vs comprehensive tests: These are verification tests. Comprehensive coverage is TEST phase work.\n\nIf you hit a blocker, STOP and `SendMessage` it to the lead.\n\nTask: [user's task description]")`
2. `TaskUpdate(taskId, owner="{specialist-name}")`
3. `Task(name="{specialist-name}", team_name="{team_name}", subagent_type="pact-{specialist-type}", prompt="You are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **`SendMessage`**: Teammates send blockers and algedonic signals directly to the lead
- **`TaskList`**: Check for tasks with blocker metadata or stalled status

On signal detected: Follow Signal Task Handling in [CLAUDE.md](../CLAUDE.md).

For agent stall detection and recovery, see [Agent Stall Detection](orchestrate.md#agent-stall-detection).

---

## Auditor Dispatch

An auditor is dispatched alongside coders unless explicitly skipped. To skip, state: "Auditor skipped: [justification]". See the [Concurrent Audit Protocol](../protocols/pact-audit.md) for full details.

**Dispatch is mandatory when**:
- Variety score >= 7 (Medium or higher)
- 3+ coders running in parallel (coordination complexity warrants observation)
- Task touches security-sensitive code (auth, crypto, user input handling)
- Domain has prior history of architecture drift (from pact-memory calibration data)

**Valid skip reasons**: Single coder on familiar pattern, variety reassessed below 7, user requested skip.

When dispatching an auditor, create its task with `metadata: {"completion_type": "signal"}` so the completion gate accepts `audit_summary` instead of standard HANDOFF.

---

## After Specialist Completes

- [ ] **Receive handoff** from specialist(s)
- [ ] Agent tasks marked `completed` (agents self-manage their task status via `TaskUpdate`)
- [ ] **Agreement verification**: `SendMessage` to specialist to confirm shared understanding of deliverables before committing. Background: [pact-ct-teachback.md](../protocols/pact-ct-teachback.md).
- [ ] **Run tests** — verify work passes. If tests fail → return to specialist for fixes (create new agent task, repeat).
- [ ] **Create atomic commit(s)** — stage and commit before proceeding
- [ ] **Calibration** — The secretary gathers calibration metrics during HANDOFF processing. When asked, provide a brief difficulty assessment: was actual difficulty higher, lower, or about the same as predicted? Which dimensions surprised you?
- [ ] **Process specialist HANDOFFs** (non-blocking):
  ```
  TaskCreate(subject="secretary: process pending HANDOFFs",
    description="Read TaskList for all completed tasks owned by agents. Cross-reference with breadcrumb file at ~/.claude/teams/{team_name}/completed_handoffs.jsonl for temporal ordering. Review each HANDOFF via TaskGet, extract institutional knowledge, save to pact-memory. Delete breadcrumb file when done. Report summary when done. If no completed agent tasks and no breadcrumb file, report 'no pending HANDOFFs' and complete.")
  TaskUpdate(taskId, owner="secretary")
  ```
- [ ] **Verify agent task completion**: On receiving each HANDOFF summary via SendMessage, check the agent's task status via TaskList. If still "in_progress", mark it completed: `TaskUpdate(taskId, status="completed")`.
- [ ] **`TaskUpdate`**: Feature task status = "completed"

> ⚠️ **Do NOT shut down specialists until the user decides the next step.** Ask first, then act.

**Next steps** — After commit, use `AskUserQuestion` to ask: "Work committed. What next?"

| User's decision | Specialists | Next action |
|----------------|-------------|-------------|
| **Yes, create PR** (Recommended) | **Keep alive** — review often needs the original specialist to fix findings | Invoke `/PACT:peer-review`. Shut down after all remediation complete + user merge decision (via `AskUserQuestion`). |
| **Continue working** | **Keep alive** — apply Reuse vs. Spawn table for follow-up | Do nothing — let the user continue. More work may follow via `/PACT:comPACT` or `/PACT:orchestrate`. |
| **Pause work for now** | **Shut down after consolidation** — pause preserves knowledge | Invoke `/PACT:pause` — consolidates memory, persists state, shuts down teammates. Worktree persists; resume later. |

**If blocker reported**:

Examples of blockers:
- Task requires a different specialist's domain
- Missing dependencies, access, or information
- Same error persists after multiple fix attempts
- Scope exceeds self-contained capability (needs PREPARE/ARCHITECT phases)
- Concurrent agents have unresolvable conflicts

When blocker is reported:
1. Receive blocker report from specialist
2. Run `/PACT:imPACT` to triage
3. May escalate to `/PACT:orchestrate` if task exceeds self-contained scope

---

## When to Escalate

Recommend `/PACT:orchestrate` instead if:
- Sub-tasks have shared-file dependencies requiring sequenced coordination
- Task requires PREPARE or ARCHITECT phases (significant research or design decisions)
- Architectural decisions affect multiple components
- Full preparation/architecture documentation is needed

### Variety-Aware Escalation

During comPACT execution, if you discover the task is more complex than expected:

| Discovery | Variety Signal | Action |
|-----------|----------------|--------|
| Sub-tasks have shared-file dependencies | Medium+ (7+) | Escalate to `/PACT:orchestrate` |
| Significant ambiguity/uncertainty | High (11+) | Escalate; may need PREPARE phase |
| Architectural decisions required | High (11+) | Escalate; need ARCHITECT phase |
| Higher risk than expected | High (11+) | Consider `/PACT:plan-mode` first |

**Heuristic**: If re-assessing variety would now score Medium+ (7+), escalate.

**Conversely**, if the specialist reports the task is simpler than expected:
- Note in handoff to orchestrator
- Complete the task; orchestrator may simplify remaining work
