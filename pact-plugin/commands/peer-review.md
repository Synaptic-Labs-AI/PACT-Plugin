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
4. TaskCreate: Reviewer tasks (architect, test-engineer, domain specialists)
5. Spawn reviewer teammates into the session team
6. Reviewers submit review plans for lead approval
7. After approval, reviewers analyze and send reviews via SendMessage
8. Collect reviews as messages arrive
9. Shutdown reviewers after all reviews received
10. TaskUpdate: Reviewer tasks status = "completed"
11. Synthesize findings
12. If major issues:
    a. TaskCreate: Remediation agent tasks
    b. Spawn remediation teammates, collect fixes via SendMessage
    c. Shutdown remediation teammates
    d. TaskUpdate: Remediation tasks status = "completed"
13. TaskCreate: "User: review minor issues" step task
14. Present minor issues to user, record decisions in step metadata
15. TaskUpdate: Step task status = "completed"
16. If "fix now" decisions:
    a. TaskCreate: Remediation agent tasks
    b. Spawn remediation teammates, collect fixes via SendMessage
    c. Shutdown remediation teammates
    d. TaskUpdate: Remediation tasks status = "completed"
17. TaskCreate: "Awaiting merge decision" approval task
18. Present to user, await approval
19. On approval: TaskUpdate approval task status = "completed"
20. TaskUpdate: Review task status = "completed", metadata.artifact = PR URL
```

> **Convention**: Synchronous user steps (step tasks, approval tasks) skip the `in_progress` transition —they go directly from `pending` to `completed` since the orchestrator handles them inline without background dispatch.

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
├── Cycle N: remediation tasks → re-review (verify-only) → check
├── After 2 failed cycles: BLOCKER task → addBlockedBy review → /PACT:imPACT
└── On resolution: blocker completed → review resumes
```

**Key rules**: Review stays `in_progress` until merge-ready; fresh tasks per cycle; re-review is verify-only (minimal scope); imPACT escalation blocks (doesn't complete/delete) review; resume after resolution.

---

## Spawning Reviewer Teammates

Pull request reviews should mirror real-world team practices where multiple reviewers sign off before merging. Spawn **at least 3 reviewer teammates** to provide comprehensive review coverage.

Standard reviewer combination:
- **pact-architect**: Design coherence, architectural patterns, interface contracts, separation of concerns
- **pact-test-engineer**: Test coverage, testability, performance implications, edge cases
- **Domain specialist coder** (selected below): Implementation quality specific to the domain

Select the domain coder based on PR focus:
- Frontend changes →**pact-frontend-coder** (UI implementation quality, accessibility, state management)
- Backend changes →**pact-backend-coder** (Server-side implementation quality, API design, error handling)
- Database changes →**pact-database-engineer** (Query efficiency, schema design, data integrity)
- Multiple domains →Coder for domain with most significant changes, or all relevant domain coders if changes are equally significant

### Reviewer Spawn Pattern

Spawn all reviewers into the existing session team. Reviewers use `mode="plan"` so they submit a review plan before analyzing.

```
Task(
  subagent_type="pact-architect",
  team_name="{team}",
  name="architect-reviewer",
  mode="plan",
  prompt="..."
)

Task(
  subagent_type="pact-test-engineer",
  team_name="{team}",
  name="test-reviewer",
  mode="plan",
  prompt="..."
)

Task(
  subagent_type="{domain-coder}",
  team_name="{team}",
  name="{domain}-reviewer",
  mode="plan",
  prompt="..."
)
```

### Reviewer Prompt Template

```
PEER REVIEW —You are a reviewer teammate on this PR.

PR: {PR URL or description}
Branch: {branch name}
Changed files: {list of changed files}

As the {role} specialist, review the PR for:
{role-specific review criteria from the reviewer combination above}

WORKFLOW:
1. Submit a review plan (via ExitPlanMode) describing what you will examine and why.
2. After plan approval, perform the review.
3. Send your review to the lead via SendMessage with the format below.
4. Mark your task complete via TaskUpdate.

REVIEW FORMAT (send via SendMessage to "team-lead"):
- Summary: One-paragraph assessment
- Findings table:
  | Finding | Severity | File(s) | Details |
  |---------|----------|---------|---------|
  | ... | Blocking/Minor/Future | ... | ... |
- Recommendation: Approve / Request Changes
```

### Plan Approval for Reviewers

Review each reviewer's plan to ensure it covers the reviewer's assigned domain. If rejecting, provide specific guidance on what to cover.

### Collecting Reviews

As each review arrives via SendMessage:
1. Record the review content
2. Mark the reviewer's task as `completed`
3. When all reviews are collected, shut down reviewers and proceed to synthesis

---

## Output Conciseness

**Default: Concise output.** User sees synthesis, not each reviewer's full output restated.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Each reviewer's raw output | Recommendations table + `See docs/review/` |
| Reviewer selection reasoning | `Spawning architect + test engineer + backend reviewer` |
| Agreement/conflict analysis details | `Ready to merge` or `Changes requested: [specifics]` |

**User can always ask** for full reviewer output (e.g., "What did the architect say?" or "Show me all findings").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "The architect found X, the test engineer found Y..." | Consolidated summary in `docs/review/` |
| "Let me synthesize the findings from all reviewers..." | (just do it, show result) |

---

**After all reviews collected and reviewers shut down**:
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
       - Single-domain items →`/PACT:comPACT` (invoke concurrently if independent)
       - Multi-domain items →`/PACT:orchestrate`
       - Mixed (both single and multi-domain) →Use `/PACT:comPACT` for the single-domain batch AND `/PACT:orchestrate` for the multi-domain batch (can run in parallel if independent)
     - After all fixes complete, re-run review to verify fixes only (not a full PR re-review)
     - **Termination**: If blocking items persist after 2 fix-verify cycles →escalate via `/PACT:imPACT`
   - **Minor + Future**:

     **Step A —Initial Gate Question** (Yes/No only):
     - Use `AskUserQuestion` tool: "Would you like to review the minor and future recommendations?"
       - Options: **Yes** (review each item) / **No** (skip to merge readiness)
     - If **No**: Skip to step 4 directly
     - If **Yes**: Continue to Step B

     **Step B —Preemptive Context Gathering**:
     - Before asking per-recommendation questions, gather and present context for ALL minor and future recommendations
     - For each recommendation, provide:
       - Why it matters (impact on code quality, maintainability, security, performance)
       - What the change would involve (scope, affected areas)
       - Trade-offs of addressing vs. not addressing
     - Keep each entry concise (2-3 sentences per bullet).
     - Present as a formatted list (one entry per recommendation) so user can review all context at once.
     - After presenting all context, proceed to Step C.

     **Step C —Per-Recommendation Questions** (after context presented):
     - Use `AskUserQuestion` tool with one question per recommendation
     - For each **minor** recommendation, ask "Address [recommendation] now?" with options:
       - **Yes** —Fix it in this PR
       - **No** —Skip for now
       - **More context** —Get additional details (if more detail is needed)
     - For each **future** recommendation, ask "What would you like to do with [recommendation]?" with options:
       - **Create GitHub issue** —Track for future work
       - **Skip** —Don't track or address
       - **Address now** —Fix it in this PR
       - **More context** —Get additional details (if more detail is needed)
     - Note: Tool supports 2-4 options per question and 1-4 questions per call. If >4 recommendations exist, make multiple `AskUserQuestion` calls to cover all items.
       - **Handling "More context" responses**:
         - When user selects "More context", provide deeper explanation beyond the preemptive context (e.g., implementation specifics, examples, related patterns)
         - After providing additional context, re-ask the same question for that specific recommendation (without the "More context" option)
         - Handle inline: provide context immediately, get the answer, then continue to the next recommendation
       - **Collect all answers first**, then batch work:
         - Group all minor=Yes items AND future="Address now" items →Select workflow based on combined scope:
           - Single-domain items →`/PACT:comPACT` (invoke concurrently if independent)
           - Multi-domain items →`/PACT:orchestrate`
         - Group all future="Create GitHub issue" items →Create GitHub issues
       - If any items fixed (minor or future addressed now) →re-run review to verify fixes only (not a full PR re-review)

4. State merge readiness (only after ALL blocking fixes complete AND minor/future item handling is done): "Ready to merge" or "Changes requested: [specifics]"

5. Present to user and **stop** —merging requires explicit user authorization (S5 policy)

---

## Signal Monitoring

Monitor for blocker/algedonic signals:
- After spawning each reviewer
- After plan approval for each reviewer
- When reviewer sends their review
- After each remediation dispatch
- On any unexpected teammate stoppage

On signal detected: Follow Signal Task Handling in CLAUDE.md.

---

**After user-authorized merge**:
1. Merge the PR (`gh pr merge`)
2. Run `/PACT:pin-memory` to update the project `CLAUDE.md` with the latest changes
3. Invoke `/PACT:worktree-cleanup` for the feature worktree
4. Report: "PR merged, memory updated, worktree cleaned up"
