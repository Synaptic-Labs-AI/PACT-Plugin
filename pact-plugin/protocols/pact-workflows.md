## The PACT Workflow Family

| Workflow | When to Use | Key Idea |
|----------|-------------|----------|
| **PACT** | Complex/greenfield work | Context-aware multi-agent orchestration |
| **plan-mode** | Before complex work, need alignment | Multi-agent planning consultation, no implementation |
| **comPACT** | Focused, independent tasks | Dispatch concurrent specialists for self-contained tasks. No PACT phases needed. |
| **rePACT** | Complex sub-tasks within orchestration | Recursive nested P→A→C→T cycle (single or multi-domain) |
| **imPACT** | When blocked or need to iterate | Triage: Redo prior phase? Additional agents needed? |
| **pause** | PR open, not ready to merge | Consolidate memory, persist state, shut down teammates |

### Lead-vs-Teammate Completion Responsibilities (per workflow)

| Workflow | Teammate completion authority | Lead completion authority |
|---|---|---|
| `/PACT:orchestrate` | None (write HANDOFF, idle on `awaiting_lead_completion`) | All teammate-owned phase tasks; the feature task |
| `/PACT:comPACT` | None for normal specialists; auditor self-completes signal-tasks | All teammate-owned tasks; the parent comPACT task |
| `/PACT:rePACT` | None (write HANDOFF, idle) | All sub-scope tasks; the parent rePACT task |
| `/PACT:peer-review` | None (reviewer writes review HANDOFF, idles) | All reviewer tasks; the peer-review parent task |
| `/PACT:plan-mode` | None (consultant writes consultation HANDOFF, idles) | All consultant tasks; the plan-mode parent task |
| `/PACT:imPACT` | None (triage agent writes triage HANDOFF, idles) | All triage tasks; the imPACT parent task |

Carve-outs apply across all workflows: signal-tasks (auditor), memory-save (secretary), force-termination (imPACT). See [pact-completion-authority.md](pact-completion-authority.md) for the full acceptance + rejection recipes and carve-out rationale; [orchestration §Completion Authority](../skills/orchestration/SKILL.md#completion-authority) holds the slim team-lead-side summary.

---

## plan-mode Protocol

**Purpose**: Multi-agent planning consultation before implementation. Get specialist perspectives synthesized into an actionable plan.

**When to use**:
- Complex features where upfront alignment prevents rework
- Tasks spanning multiple specialist domains
- When you want user approval before implementation begins
- Greenfield work with significant architectural decisions

**Four phases**:

| Phase | What Happens |
|-------|--------------|
| 0. Analyze | Orchestrator assesses scope, selects relevant specialists |
| 1. Consult | Specialists provide planning perspectives in parallel |
| 2. Synthesize | Orchestrator resolves conflicts, sequences work, assesses risk |
| 3. Present | Save plan to `docs/plans/`, present to user, await approval |

**Key rules**:
- **No implementation** — planning consultation only
- **No git branch** — that happens when `/PACT:orchestrate` runs
- Specialists operate in "planning-only mode" (analysis, not action)
- Conflicts surfaced and resolved (or flagged for user decision)

**Output**: `docs/plans/{feature-slug}-plan.md`

**After approval**: User runs `/PACT:orchestrate {task}`, which references the plan.

**When to recommend alternatives**:
- Trivial task → `/PACT:comPACT`
- Unclear requirements → Ask clarifying questions first
- Need research before planning → Run preparation phase alone first

---

## imPACT Protocol

**Trigger when**: Blocked; get similar errors repeatedly; or prior phase output is wrong.

**Diagnostic inputs**: Before triaging, check available signals — progress signal history (if monitoring was requested) reveals whether the agent was converging, exploring, or stuck. Apply the Conversation Failure Taxonomy after choosing an outcome.

**Three questions**:
1. **Redo prior phase?** — Is the issue upstream in P→A→C→T?
2. **Additional agents needed?** — Do we need help beyond the blocked agent's scope/specialty?
3. **Is the agent recoverable?** — Can the blocked agent be resumed or helped, or is it unrecoverable (looping, stalled, context exhausted)?

**Six outcomes**:
| Outcome | When | Action |
|---------|------|--------|
| Redo prior phase | Issue is upstream in P→A→C→T | Re-delegate to relevant agent(s) to redo the prior phase |
| Augment present phase | Need help in current phase | Re-invoke blocked agent with additional context + parallel agents |
| Invoke rePACT | Sub-task needs own P→A→C→T cycle | Use `/PACT:rePACT` for nested cycle |
| Terminate agent | Agent unrecoverable (infinite loop, context exhaustion, stall after resume) | `TaskStop(task_id=taskId)` (force-stop) + `TaskUpdate(taskId, status="completed", metadata={"terminated": true, "reason": "..."})` + fresh spawn with partial handoff |
| Not truly blocked | Neither question is "Yes" | Instruct agent to continue with clarified guidance |
| Escalate to user | 3+ imPACT cycles without resolution | Proto-algedonic signal—systemic issue needs user input |

**Conversation Failure Taxonomy** (diagnostic lens — apply after choosing outcome):

| Type | Symptoms | Recovery |
|------|----------|----------|
| Misunderstanding | Wrong output, no errors | Teachback correction + corrected context |
| Derailment | Loops on same error | Fresh agent, different framing |
| Discontinuity | Lost/stale context | Reconstruct from memory/TaskGet |
| Absence | Insufficient upstream output | Redo prior phase |

---

## comPACT Protocol

**Core idea**: Dispatch concurrent specialists for self-contained tasks. No PACT phases needed. Use orchestrate when phases need to chain — research informing design, design informing code.

comPACT handles tasks that can be decomposed into independent sub-tasks — single-domain or cross-domain — without shared-file dependencies. For independent sub-tasks, it invokes multiple specialists in parallel.

**Available specialists**:
| Shorthand | Specialist | Use For |
|-----------|------------|---------|
| `backend` | pact-backend-coder | Server-side logic, APIs, middleware |
| `frontend` | pact-frontend-coder | UI, React, client-side |
| `database` | pact-database-engineer | Schema, queries, migrations |
| `prepare` | pact-preparer | Research, requirements |
| `test` | pact-test-engineer | Standalone test tasks |
| `architect` | pact-architect | Design guidance, pattern selection |
| `devops` | pact-devops-engineer | CI/CD, Docker, scripts, infrastructure |
| `security` | pact-security-engineer | Security audit of existing code |
| `qa` | pact-qa-engineer | Runtime verification of app behavior |

**Smart specialist selection**:
- *Clear task* → Auto-select (domain keywords, file types, single-domain action)
- *Ambiguous task* → Ask user which specialist

### When to Invoke Multiple Specialists

**MANDATORY: parallel unless tasks share files or have dependencies.** comPACT invokes multiple agents — same type or mixed types — for independent items.

Invoke multiple specialists when:
- Multiple independent items (bugs, components, endpoints)
- No shared files between sub-tasks
- No data or ordering dependencies between sub-tasks

| Task | Agents Invoked |
|------|----------------|
| "Fix 3 backend bugs" | 3 backend-coders (parallel) |
| "Add validation to 5 endpoints" | Multiple backend-coders (parallel) |
| "Update styling on 3 components" | Multiple frontend-coders (parallel) |
| "Add API endpoint + update DB index" | 1 backend-coder + 1 database-engineer (parallel, independent files) |
| "Fix CSS layout + add server logging" | 1 frontend-coder + 1 backend-coder (parallel, no shared files) |

### Pre-Invocation (Required)

1. **Set up worktree** — If already in a worktree for this feature, reuse it. Otherwise, invoke `/PACT:worktree-setup` with the feature branch name. All subsequent work happens in the worktree.
2. **Verify session team exists** — The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.
3. **S2 coordination** (if concurrent) — Check for file conflicts, assign boundaries

### S2 Light Coordination (for parallel comPACT)

1. **Check for conflicts** — Do any sub-tasks touch the same files?
2. **Assign boundaries** — If conflicts exist, sequence or define clear boundaries
3. **Set convention authority** — First agent's choices become standard for the batch
4. **Environment drift** — When dispatching subsequent agents after earlier agents complete, check `file-edits.json` for files modified since last dispatch and include relevant deltas in prompts

### Specialist instructions (injected when invoking specialist)

- Work directly from task description
- Check docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist—reference relevant context
- Do not create new documentation artifacts
- Smoke tests only: Verify it compiles, runs, and happy path doesn't crash (no comprehensive unit tests—that's TEST phase work)
- For parallel dispatch or novel domains: include "Send progress signals per the agent-teams skill Progress Signals section" in dispatch prompt

**Escalate to `/PACT:orchestrate` when**:
- Sub-tasks have shared-file dependencies requiring sequenced coordination
- Task requires PREPARE or ARCHITECT phases (significant research or design decisions)
- Specialist reports a blocker (run `/PACT:imPACT` first)

### Auditor Dispatch

An auditor is dispatched alongside coders unless explicitly skipped. To skip, output on its own line so the decision is visible to the user:

> **Auditor skipped**: [justification]

See the [Concurrent Audit Protocol](pact-audit.md) for full details.

**Dispatch is mandatory when**:
- Variety score >= 7 (Medium or higher)
- 3+ coders running in parallel (coordination complexity warrants observation)
- Task touches security-sensitive code (auth, crypto, user input handling)
- Domain has prior history of architecture drift (from pact-memory calibration data)

**Valid skip reasons**: Single coder on familiar pattern, variety reassessed below 7, user requested skip.

### After Specialist Completes

1. **Receive handoff** from specialist(s)
2. **Verify deliverables** — confirm files listed in "Produced" were actually modified (e.g., `git diff --stat`, line counts, grep checks). Never report completion based solely on agent handoff.
3. **Run tests** — verify work passes. If tests fail → return to specialist for fixes before committing.
4. **Create atomic commit(s)** — stage and commit before proceeding
5. **Calibration** — The secretary gathers calibration metrics during HANDOFF processing. When asked, provide a brief difficulty assessment: was actual difficulty higher, lower, or about the same as predicted? Which dimensions surprised you?

**Next steps** — After commit, ask: "Work committed. Create PR?"
- Yes (Recommended) → invoke `/PACT:peer-review`
- Not yet / pause → invoke `/PACT:pause` — consolidates memory, persists state, shuts down teammates. Worktree persists; resume later.
- More work → continue with comPACT or orchestrate

**If blocker reported**:
1. Receive blocker from specialist
2. Run `/PACT:imPACT` to triage
3. May escalate to `/PACT:orchestrate` if task exceeds single-specialist scope

---
