---
description: Peer review of current work (commit, create PR, multi-agent review)
argument-hint: [e.g., feature X implementation]
---
Review the current work: $ARGUMENTS

1. Commit any uncommitted work
2. Create a PR if one doesn't exist
3. Review the PR

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
    c. Dispatch, monitor until complete
    d. TaskUpdate: Remediation tasks status = "completed"
12. TaskCreate: "User: review minor issues" step task
13. Present minor issues to user, record decisions in step metadata
14. TaskUpdate: Step task status = "completed"
15. If "fix now" decisions:
    a. TaskCreate: Remediation agent tasks
    b. TaskUpdate: Remediation tasks status = "in_progress"
    c. Dispatch, monitor until complete
    d. TaskUpdate: Remediation tasks status = "completed"
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

**Key rules**: Review stays `in_progress` until merge-ready; fresh tasks per cycle; re-review is verify-only (minimal scope); imPACT escalation blocks (doesn't complete/delete) review; resume after resolution.

### Reviewer-to-Fixer Reuse

> ⚠️ **MANDATORY**: When a reviewer's findings need fixing in their domain, send the fix task directly to the reviewer via `SendMessage`. Do NOT shut down reviewers and spawn a fresh coder — the reviewer has the most relevant context (files loaded, issues understood, line numbers identified).

| Situation | Action |
|-----------|--------|
| Reviewer identified issues in their domain | **Reuse** reviewer as fixer via `SendMessage` |
| Fixes span a different domain | **Spawn** domain specialist (reviewer stays for consultation) |
| Multiple independent fixes in parallel | **Spawn** additional agents alongside reused reviewer |

---

**PR Review Workflow**

If no team exists for this branch: `TeamCreate(team_name="pact-{branch-name}")`. Reuse existing team if one exists.

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
3. `Task(name="{reviewer-name}", team_name="pact-{branch}", subagent_type="pact-{reviewer-type}", prompt="You are joining team pact-{branch}. Check TaskList for tasks assigned to you.")`

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
