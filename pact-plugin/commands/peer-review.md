---
description: Peer review of current work (commit, create PR, multi-agent review)
argument-hint: [e.g., feature X implementation]
---
Review the current work: $ARGUMENTS

1. Verify all agent commits are present (agents commit their own work before HANDOFF)
2. Commit any remaining cross-agent or orchestrator-generated changes
3. Create a PR if one doesn't exist
4. Review the PR

---

## Task Hierarchy

Create a review Task hierarchy:

```
1. TaskCreate: Review task "Review: {feature}"
2. TaskUpdate: Review task status = "in_progress"
3. Analyze PR: Which reviewers needed?
4. TaskCreate: Reviewer agent tasks (architect, test-engineer, domain specialists)
5. TaskUpdate: Reviewer tasks status = "in_progress"
6. TaskUpdate: Review task addBlockedBy = [reviewer IDs]
7. Dispatch reviewers in parallel
8. Monitor until reviewers complete
9. TaskUpdate: Reviewer tasks status = "completed" (as each completes)
10. Synthesize findings
11. If major issues:
    a. TaskCreate: Remediation agent tasks
    b. TaskUpdate: Remediation tasks status = "in_progress"
    c. Dispatch, monitor until complete (agents commit their own fixes)
    d. Verify agent commits exist (from HANDOFF commit hashes)
    e. Run integration verification (see below)
    f. TaskUpdate: Remediation tasks status = "completed"
12. TaskCreate: "User: review minor issues" step task
13. Present minor issues to user, record decisions in step metadata
14. TaskUpdate: Step task status = "completed"
15. If "fix now" decisions:
    a. TaskCreate: Remediation agent tasks
    b. TaskUpdate: Remediation tasks status = "in_progress"
    c. Dispatch, monitor until complete (agents commit their own fixes)
    d. Verify agent commits exist (from HANDOFF commit hashes)
    e. Run integration verification (see below)
    f. TaskUpdate: Remediation tasks status = "completed"
16. TaskCreate: "Awaiting merge decision" approval task
17. Present to user, await approval
18. On approval: TaskUpdate approval task status = "completed"
19. TaskUpdate: Review task status = "completed", metadata.artifact = PR URL
```

> **Convention**: Synchronous user steps (step tasks, approval tasks) skip the `in_progress` transition — they go directly from `pending` to `completed` since the orchestrator handles them inline without background dispatch.

**Example structure:**
```
[Review] "Review: user authentication"
├── [Agent] "architect: design review"
├── [Agent] "test-engineer: coverage review"
├── [Agent] "backend-coder: implementation review"
├── [Remediation] (dynamic, for major issues)
│   └── [Agent] "fix: auth vulnerability"
├── [Step] "User: review minor issues"
├── [Remediation] (dynamic, for "fix now" minors)
│   └── [Agent] "fix: input validation"
└── [Approval] "Awaiting merge decision"
```

## Remediation Task State

```
Review task: in_progress (persists until merge-ready)
├─ Cycle N: remediation tasks → re-review (verify-only) → check
├─ After 2 failed cycles: BLOCKER task → addBlockedBy review → /PACT:imPACT
└─ On resolution: blocker completed → review resumes
```

**Key rules**: Review stays `in_progress` until merge-ready; fresh tasks per cycle; re-review is verify-only (minimal scope); after 2 failed fix-verify cycles, escalate via `/PACT:imPACT` which triages whether to redo a prior phase, bring in additional agents, or escalate to user; imPACT escalation blocks (doesn't complete/delete) review; resume after resolution.

### Reviewer-to-Fixer Reuse

> ⚠️ **MANDATORY**: When a reviewer's findings need fixing in their domain, send the fix task directly to the reviewer via `SendMessage`. Do NOT shut down reviewers and spawn a fresh coder — the reviewer has the most relevant context (files loaded, issues understood, line numbers identified).

| Situation | Action |
|-----------|--------|
| Reviewer identified issues in their domain | **Reuse** reviewer as fixer via `SendMessage` |
| Fixes span a different domain | **Spawn** domain specialist (reviewer stays for consultation) |
| Multiple independent fixes in parallel | **Spawn** additional agents alongside reused reviewer |

### Integration Verification

After all parallel remediation agents complete and commit, the orchestrator runs integration verification to confirm that fixes don't conflict with each other and don't break the existing codebase. This is a compatibility gate, not a comprehensive test phase.

```
1. All remediation agents complete → verify commit hashes from HANDOFFs
2. Run test suite to check for regressions or conflicts between fixes
3. If conflicts or failures found:
   a. Identify which agent's changes conflict
   b. Route back to the relevant agent(s) via SendMessage for resolution
   c. Re-run integration verification after fixes
4. Only proceed to verify-only re-review after verification passes
```

> **Who runs this**: The orchestrator — this is an operational gate (S2 coordination), not specialist work.

### Verify-Only Re-Review Protocol

When remediation fixes are complete and integration verification passes, a verify-only re-review confirms that findings were addressed. This is NOT a fresh full review.

| Aspect | Verify-Only Re-Review | Full Review |
|--------|----------------------|-------------|
| **Scope** | Only files changed during remediation (use commit hashes from agent HANDOFFs to determine the diff) | Entire PR |
| **Depth** | Spot-check that each finding was addressed | Full architectural, quality, and implementation review |
| **Agent** | `SendMessage` to the reviewer who found the issues (they are in consultant mode — reuse them) | Spawn dedicated reviewer agents |
| **Format** | Checklist: "Finding X: Resolved / Not Resolved / New Issue Introduced" | Full HANDOFF with findings table |
| **Duration** | Significantly faster than initial review | Full review cycle |

**Dispatch**: Send each original reviewer a `SendMessage` with:
- The list of findings they raised that were marked as blocking
- The commit hashes from the remediation agent's HANDOFF (so they can diff)
- Request: "Verify each finding is resolved. Report back with checklist."

**Outcome**:
- All findings resolved → proceed to merge readiness
- Any "Not Resolved" or "New Issue Introduced" → new remediation cycle (fresh tasks)
- After 2 failed fix-verify cycles → escalate via `/PACT:imPACT`

### Pattern: Parallel Domain-Batched Remediation

When review findings span multiple domains, batch them by domain and dispatch one remediation agent per domain in parallel (comPACT-style). This pattern is implicitly supported by the concurrent dispatch guidance — naming it makes it explicit and referenceable.

**Steps**:
1. **Batch findings by domain** — Group blocking items by specialist domain (backend, frontend, database, etc.)
2. **S2 coordination** — Verify no file conflicts between domain batches before dispatch
3. **Dispatch one agent per domain** — Use Reviewer-to-Fixer Reuse where applicable (reviewer becomes fixer for their domain)
4. **Integration verification** — After all agents complete and commit, run integration verification (see above)
5. **Verify-only re-review** — SendMessage to original reviewers to confirm findings are resolved (see above)

**When to use**: Multiple blocking findings across 2+ specialist domains. For single-domain findings, standard remediation dispatch is sufficient.

**Example**:
```
Review findings: 3 blocking items
├── Backend: auth validation missing, error handling incomplete → backend-coder (or reuse backend reviewer)
├── Frontend: XSS in user input display → frontend-coder (or reuse frontend reviewer)
└── After both complete:
    ├── Integration verification (orchestrator runs tests)
    └── Verify-only re-review (SendMessage to reviewers)
```

---

**PR Review Workflow**

**Verify session team exists**: The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.

Pull request reviews should mirror real-world team practices where multiple reviewers sign off before merging. Dispatch **at least 3 reviewers in parallel** to provide comprehensive review coverage:

Standard reviewer combination:
- **pact-architect**: Design coherence, architectural patterns, interface contracts, separation of concerns
- **pact-test-engineer**: Test coverage, testability, performance implications, edge cases
- **Domain specialist coder** (selected below): Implementation quality specific to the domain

Select the domain coder based on PR focus:
- Frontend changes → **pact-frontend-coder** (UI implementation quality, accessibility, state management)
- Backend changes → **pact-backend-coder** (Server-side implementation quality, API design, error handling)
- Database changes → **pact-database-engineer** (Query efficiency, schema design, data integrity)
- Multiple domains → Coder for domain with most significant changes, or all relevant domain coders if changes are equally significant

**Dispatch reviewers**:

For each reviewer:
1. `TaskCreate(subject="{reviewer-type}: review {feature}", description="Review this PR. Focus: [domain-specific review criteria]...")`
2. `TaskUpdate(taskId, owner="{reviewer-name}")`
3. `Task(name="{reviewer-name}", team_name="{team_name}", subagent_type="pact-{reviewer-type}", prompt="You are joining team {team_name}. Check TaskList for tasks assigned to you.")`

Spawn all reviewers in parallel (multiple `Task` calls in one response).

---

## Output Conciseness

**Default: Concise output.** User sees synthesis, not each reviewer's full output restated.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Each reviewer's raw output | Recommendations table + `See docs/review/` |
| Reviewer selection reasoning | `Invoking architect + test engineer + backend coder` |
| Agreement/conflict analysis details | `Ready to merge` or `Changes requested: [specifics]` |

**User can always ask** for full reviewer output (e.g., "What did the architect say?" or "Show me all findings").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "The architect found X, the test engineer found Y..." | Consolidated summary in `docs/review/` |
| "Let me synthesize the findings from all reviewers..." | (just do it, show result) |

---

**After all reviews complete**:
1. Synthesize findings into a unified review summary with consolidated recommendations
2. Present **all** findings to user as a **markdown table** **before asking any questions** (blocking, minor, and future):

   | Recommendation | Severity | Reviewer |
   |----------------|----------|----------|
   | [the finding]  | Blocking / Minor / Future | architect / test / backend / etc. |

   - **Blocking**: Must fix before merge
   - **Minor**: Optional fix for this PR
   - **Future**: Out of scope; track as GitHub issue

3. Handle recommendations by severity:
   - **No recommendations**: If the table is empty (no blocking, minor, or future items), proceed directly to step 4.
   - **Blocking**: Automatically address all blocking items:
     - Batch fixes by selecting appropriate workflow(s) based on combined scope:
       - Single-domain items → `/PACT:comPACT` (invoke concurrently if independent)
       - Multi-domain items → `/PACT:orchestrate`
       - Mixed (both single and multi-domain) → Use `/PACT:comPACT` for the single-domain batch AND `/PACT:orchestrate` for the multi-domain batch (can run in parallel if independent)
     - After all fixes complete, re-run review to verify fixes only (not a full PR re-review)
     - **Termination**: If blocking items persist after 2 fix-verify cycles → escalate via `/PACT:imPACT`
   - **Minor + Future**:

     **Step A — Initial Gate Question** (Yes/No only):
     - Use `AskUserQuestion` tool: "Would you like to review the minor and future recommendations?"
       - Options: **Yes** (review each item) / **No** (skip to merge readiness)
     - If **No**: Skip to step 4 directly
     - If **Yes**: Continue to Step B

     **Step B — Preemptive Context Gathering**:
     - Before asking per-recommendation questions, gather and present context for ALL minor and future recommendations
     - For each recommendation, provide:
       - Why it matters (impact on code quality, maintainability, security, performance)
       - What the change would involve (scope, affected areas)
       - Trade-offs of addressing vs. not addressing
     - Keep each entry concise (2-3 sentences per bullet).
     - Present as a formatted list (one entry per recommendation) so user can review all context at once.
     - After presenting all context, proceed to Step C.

     **Step C — Per-Recommendation Questions** (after context presented):
     - Use `AskUserQuestion` tool with one question per recommendation
     - For each **minor** recommendation, ask "Address [recommendation] now?" with options:
       - **Yes** — Fix it in this PR
       - **No** — Skip for now
       - **More context** — Get additional details (if more detail is needed)
     - For each **future** recommendation, ask "What would you like to do with [recommendation]?" with options:
       - **Create GitHub issue** — Track for future work
       - **Skip** — Don't track or address
       - **Address now** — Fix it in this PR
       - **More context** — Get additional details (if more detail is needed)
     - Note: Tool supports 2-4 options per question and 1-4 questions per call. If >4 recommendations exist, make multiple `AskUserQuestion` calls to cover all items.
       - **Handling "More context" responses**:
         - When user selects "More context", provide deeper explanation beyond the preemptive context (e.g., implementation specifics, examples, related patterns)
         - After providing additional context, re-ask the same question for that specific recommendation (without the "More context" option)
         - Handle inline: provide context immediately, get the answer, then continue to the next recommendation
       - **Collect all answers first**, then batch work:
         - Group all minor=Yes items AND future="Address now" items → Select workflow based on combined scope:
           - Single-domain items → `/PACT:comPACT` (invoke concurrently if independent)
           - Multi-domain items → `/PACT:orchestrate`
         - Group all future="Create GitHub issue" items → Create GitHub issues
       - If any items fixed (minor or future addressed now) → re-run review to verify fixes only (not a full PR re-review)

4. State merge readiness (only after ALL blocking fixes complete AND minor/future item handling is done): "Ready to merge" or "Changes requested: [specifics]"

5. Present to user and **stop** — merging requires explicit user authorization (S5 policy)

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **SendMessage**: Teammates send blockers and algedonic signals directly to the lead
- **TaskList**: Check for tasks with blocker metadata or stalled status
- After each reviewer dispatch, after each remediation dispatch, on any unexpected stoppage

On signal detected: Follow Signal Task Handling in CLAUDE.md.

---

**After user-authorized merge**:
1. Merge the PR (`gh pr merge`)
2. Run `/PACT:pin-memory` to update the project `CLAUDE.md` with the latest changes
3. Invoke `/PACT:worktree-cleanup` for the feature worktree
4. Report: "PR merged, memory updated, worktree cleaned up"
