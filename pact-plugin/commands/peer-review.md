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
1. `TaskCreate`: Review task "Review: {feature}"
2. `TaskUpdate`: Review task status = "in_progress"
3. Analyze PR: Which reviewers needed?
4. `TaskCreate`: Reviewer agent tasks (architect, test-engineer, domain specialists)
5. `TaskUpdate`: Reviewer tasks status = "in_progress"
6. `TaskUpdate`: Review task addBlockedBy = [reviewer IDs]
7. Dispatch reviewers in parallel
8. Monitor until reviewers complete
9. `TaskUpdate`: Reviewer tasks status = "completed" (as each completes)
10. Synthesize findings
11. If major issues:
    a. `TaskCreate`: Remediation agent tasks
    b. `TaskUpdate`: Remediation tasks status = "in_progress"
    c. Dispatch, monitor until complete
    d. `TaskUpdate`: Remediation tasks status = "completed"
12. `TaskCreate`: "User: review minor issues" step task
13. Present minor issues to user, record decisions in step metadata
14. `TaskUpdate`: Step task status = "completed"
15. If "fix now" decisions:
    a. `TaskCreate`: Remediation agent tasks
    b. `TaskUpdate`: Remediation tasks status = "in_progress"
    c. Dispatch, monitor until complete
    d. `TaskUpdate`: Remediation tasks status = "completed"
16. `TaskCreate`: "Awaiting merge decision" approval task
17. Present to user, await approval
18. On approval: `TaskUpdate` approval task status = "completed"
19. `TaskUpdate`: Review task status = "completed", metadata.artifact = PR URL
```

> **Convention**: Synchronous user steps (step tasks, approval tasks) skip the `in_progress` transition — they go directly from `pending` to `completed` since the orchestrator handles them inline without background dispatch.

**Example structure:**
```
[Review] "Review: user authentication"
├── [Agent] "architect: design review"
├── [Agent] "test-engineer: coverage review"
├── [Agent] "backend-coder: implementation review"
├── [Agent] "security-engineer: security review" (conditional)
├── [Agent] "qa-engineer: runtime verification" (conditional)
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

**Persist `remediation_cycle_count`**: `TaskUpdate(reviewTaskId, metadata={"remediation_cycle_count": N})` — increment at each cycle start; default 0 if absent.

### Verify-Only Re-Review

After remediation fixes are applied, re-review is **verify-only** — not a fresh review:

| Aspect | Verify-Only | Full Review |
|--------|-------------|-------------|
| **Scope** | Only files changed during remediation | Full PR diff |
| **Depth** | Confirm each finding was addressed | Fresh architectural analysis |
| **Agent** | Reuse the reviewer who found the issues | Multiple reviewers in parallel |
| **Format** | Checklist: finding → resolved / not resolved / new issue | Full review with severity ratings |
| **Duration** | Significantly faster than initial review | Full review cycle |

> **New issue verification**: When a verify-only reviewer reports a "new issue" not in the original checklist, verify the finding against the actual file state before dispatching a fix agent. Verify-only reviewers see a narrow remediation diff and may flag issues that were already addressed in the original code or earlier commits. Check the file directly (`grep` or `Read`) before treating it as actionable.

### Post-Remediation Incremental Update

After remediation fixes are verified, create an incremental update task for the secretary to process any new findings:

```
TaskCreate(subject="secretary: incremental harvest (post-remediation)",
  description="Run Incremental Harvest for team {team_name}. Follow the Incremental Harvest workflow in your pact-handoff-harvest skill. Report delta summary when done.")
TaskUpdate(taskId, owner="secretary")
```

This trigger fires only when remediation occurred and changed things. Skip if no remediation was needed.

### Reviewer-to-Fixer Reuse

> ⚠️ **MANDATORY**: When a reviewer's findings need fixing in their domain, send the fix task directly to the reviewer via `SendMessage`. Do NOT shut down reviewers and spawn a fresh coder — the reviewer has the most relevant context (files loaded, issues understood, line numbers identified).

| Situation | Action |
|-----------|--------|
| Reviewer identified issues in their domain | **Reuse** reviewer as fixer via `SendMessage` |
| Fixes span a different domain | **Spawn** domain specialist (reviewer stays for consultation) |
| Multiple independent fixes in parallel | **Spawn** additional agents alongside reused reviewer |

> **Worktree scope reminder**: When reusing a reviewer as a fixer or spawning a new fixer, include the worktree path and `CLAUDE.md` scope note in the fix task: "`CLAUDE.md` is gitignored and does not exist in worktrees — do not edit it. If your task mentions updating `CLAUDE.md`, flag it in your HANDOFF instead."

> **Remediation stage-ready wait**: Reviewers acting as fixers stage remediation changes and notify the team-lead, then wait for the team-lead to commit the fix. Instruct the reviewer to SET the `intentional_wait` task metadata (reason `awaiting_amendment_review`, resolver `lead`) before the stage-ready notify so TeammateIdle hooks do not nag through the fix→commit→re-review cycle; CLEAR when the team-lead acknowledges the commit. See the "Intentional Waiting" section in `pact-agent-teams/SKILL.md` for the SET/CLEAR contract.

---

**PR Review Workflow**

**Verify session team exists**: The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.

Pull request reviews should mirror real-world team practices where multiple reviewers sign off before merging. Dispatch **at least 3 reviewers in parallel** to provide comprehensive review coverage:

Standard reviewer combination (always included):
- **pact-architect**: Design coherence, architectural patterns, interface contracts, separation of concerns
- **pact-test-engineer**: Test coverage, testability, performance implications, edge cases
- **Domain specialist coder** (selected below): Implementation quality specific to the domain

Conditional reviewers (included when relevant):
- **pact-security-engineer**: When PR touches auth/authorization, user input handling, API endpoints, data serialization, or crypto/token code. File-pattern heuristics:
  - Path segments: `auth`, `login`, `password`, `token`, `encrypt`, `crypto`, `session`, `permission`, `middleware/auth`, `security`
  - Input handling: files processing user input, form data, request bodies, query parameters
  - Config: files with CORS settings, CSP headers, security headers, secrets management
- **pact-qa-engineer**: When project has a runnable dev server and PR includes UI or user-facing changes

Select the domain coder based on PR focus:
- Frontend changes → **pact-frontend-coder** (UI implementation quality, accessibility, state management)
- Backend changes → **pact-backend-coder** (Server-side implementation quality, API design, error handling)
- Database changes → **pact-database-engineer** (Query efficiency, schema design, data integrity)
- Infrastructure changes → **pact-devops-engineer** (CI/CD quality, Docker best practices, script safety)
- Multiple domains → Coder for domain with most significant changes, or all relevant domain coders if changes are equally significant

**Teachback-Gated Dispatch**

Each reviewer dispatch creates **two tasks**, not one:

- **Task A** — TEACHBACK gate. `subject = "{reviewer-type}: TEACHBACK for review of {feature}"`, owner = reviewer. Description: state which review angle the reviewer is taking (consistency check vs adversarial vs design coherence) before reading the diff.
- **Task B** — primary review work. `subject = "{reviewer-type}: review {feature}"`, owner = reviewer, `blockedBy = [<Task A id>]`.

Both are created BEFORE the `Agent(...)` spawn call. The reviewer claims A, submits teachback metadata, idles on `awaiting_lead_completion`. You review the TEACHBACK (does it state the review angle clearly?), then accept via the two-call atomic pair: `SendMessage(to=reviewer, ...)` FIRST, then `TaskUpdate(A, status="completed")` — see [Teachback Review](../protocols/pact-completion-authority.md#teachback-review) for the rationale. On accept, the reviewer wakes to claim B and read the diff.

```
A_id = TaskCreate(
    subject="{reviewer-type}: TEACHBACK for review of {feature}",
    description="DOGFOOD TEACHBACK GATE.\n\n"
                "Submit TEACHBACK by writing metadata.teachback_submit (per pact-teachback skill). "
                "SET intentional_wait{reason=awaiting_lead_completion}. Idle. "
                "DO NOT mark this task completed — team-lead-only completion.\n\n"
                "Mission for Task B: see Task #{B_id}."
)
TaskUpdate(A_id, owner="{reviewer-name}")
B_id = TaskCreate(subject="{reviewer-type}: review {feature}", description="<full review mission>")
TaskUpdate(B_id, owner="{reviewer-name}", addBlockedBy=[A_id])
TaskUpdate(A_id, addBlocks=[B_id])
```

The `Agent()` `prompt` does NOT change shape — the Teachback-Gated Dispatch is encoded in the surrounding TaskCreate sequence.

---

**Dispatch reviewers** — for each reviewer, follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch):

1. `TaskCreate(subject="{reviewer-type}: TEACHBACK for review of {feature}", description="<teachback gate brief; cross-ref to Task B for the mission>")` — Task A.
2. `TaskCreate(subject="{reviewer-type}: review {feature}", description=<see below>)` — Task B.
   - Task B's `description` carries the review mission: "Review this PR. Focus: [domain-specific review criteria]…"
3. `TaskUpdate(A_id, owner="{reviewer-name}", addBlocks=[B_id])`
4. `TaskUpdate(B_id, owner="{reviewer-name}", addBlockedBy=[A_id])`
5. Spawn the reviewer with the canonical dispatch form. The `prompt` MUST lead with the `YOUR PACT ROLE: teammate ({reviewer-name})` marker on its own line so routing detects the teammate spawn (team protocol + teachback content arrive via spawn-time skills frontmatter, not a per-prompt directive):

```
Agent(
  name="{reviewer-name}",
  team_name="{team_name}",
  subagent_type="pact-{reviewer-type}",
  prompt="YOUR PACT ROLE: teammate ({reviewer-name}).\n\nYou are joining team {team_name}. Check `TaskList` for tasks assigned to you."
)
```

Spawn all reviewers in parallel (multiple `Task` calls in one response).

**Journal event**: After dispatching all reviewers, write a `review_dispatch` event:
```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] peer-review.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type review_dispatch --session-dir '{session_dir}' --stdin <<'JSON'
{"pr_number": {pr_number}, "pr_url": "{pr_url}", "reviewers": ["{reviewer1}", "{reviewer2}"]}
JSON
```

> ⚠️ **Heredoc-stdin contract**: All journal-event writes use `--stdin <<'JSON' ... JSON` (quoted delimiter). This disables bash variable expansion so apostrophes, quotes, and backticks in template-substituted values (e.g. `{first_line}`, `{finding}`, `{branch}`) pass through verbatim — fixing the silent journal-drop bug where commit messages with `don't` would close the bash quote and abort the write under `set -e`. The orchestrator must still produce JSON-valid string content (escape `\"` and `\\` and control chars when constructing the body).

**HANDOFF review** (dispatched parallel with reviewers — PRIMARY memory trigger):
```
TaskCreate(subject="secretary: harvest pending HANDOFFs (primary trigger, pre-merge)",
  description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

This is the **primary memory trigger** — fires unconditionally at reviewer dispatch (runs parallel with reviewers).

### Reviewer Teachback

Each reviewer should state their understanding of the PR's intent before diving into review. This catches cases where a reviewer misunderstands the purpose and produces irrelevant findings.

**Mechanism**: Include in each reviewer's task description:
> "Before reviewing, send a teachback message to the team-lead stating your understanding of what this PR is trying to accomplish and what you'll focus on in your domain. Format: `[{sender}→team-lead] Teachback: I understand this PR is [intent]. Reviewing with focus on [domain focus]. Proceeding unless corrected.` Non-blocking — proceed with review after sending."

This uses the same teachback mechanism as agent HANDOFFs. Background: [pact-ct-teachback.md](../protocols/pact-ct-teachback.md).

---

## Output Conciseness

See also: [Communication Charter](../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

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
2. **Journal events**: Write a `review_finding` event for each synthesized finding:
   ```bash
   set -e
   trap 'rc=$?; echo "[JOURNAL WRITE FAILED] peer-review.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
   # Repeat for each finding:
   python3 "{plugin_root}/hooks/shared/session_journal.py" write \
     --type review_finding --session-dir '{session_dir}' --stdin <<'JSON'
   {"severity": "{blocking|suggestion|nitpick}", "finding": "{one-line description}", "reviewer": "{reviewer-name}", "task_id": "{reviewer_task_id}"}
JSON
   ```
3. Present **all** findings to user as a **markdown table** **before asking any questions** (blocking, minor, and future):

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
       - Independent items (no shared files) → `/PACT:comPACT` (invoke concurrently, same or mixed domain)
       - Items with shared-file dependencies or needing PREPARE/ARCHITECT → `/PACT:orchestrate`
       - Mixed (both independent and dependent) → Use `/PACT:comPACT` for the independent batch AND `/PACT:orchestrate` for the dependent batch (can run in parallel if non-overlapping)
     - **Journal event**: Write a `remediation` event when dispatching fixes:
       ```bash
       set -e
       trap 'rc=$?; echo "[JOURNAL WRITE FAILED] peer-review.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
       python3 "{plugin_root}/hooks/shared/session_journal.py" write \
         --type remediation --session-dir '{session_dir}' --stdin <<'JSON'
       {"cycle": {cycle_number}, "items": ["{finding_id1}"], "fixer": "{agent-name}"}
JSON
       ```
     - After all fixes complete, re-run review to verify fixes only (see Verify-Only Re-Review above)
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
     - > **Tool mapping**: Every option listed above MUST appear as a named `option` in the `AskUserQuestion` call. For minor recommendations: **Yes**, **No**, **More context**. For future recommendations: **Create GitHub issue**, **Skip**, **Address now**, **More context**. Do not omit any option and rely on the tool's built-in "Other" freeform input — each is a first-class option, not a fallback.
     - Note: Tool supports 2-4 options per question and 1-4 questions per call. If >4 recommendations exist, make multiple `AskUserQuestion` calls to cover all items.
       - **Handling "More context" responses**:
         - When user selects "More context", provide deeper explanation beyond the preemptive context (e.g., implementation specifics, examples, related patterns)
         - After providing additional context, re-ask the same question for that specific recommendation (without the "More context" option)
         - Handle inline: provide context immediately, get the answer, then continue to the next recommendation
       - **Collect all answers first**, then batch work:
         - Group all minor=Yes items AND future="Address now" items → Select workflow based on combined scope:
           - Independent items (no shared files) → `/PACT:comPACT` (invoke concurrently, same or mixed domain)
           - Items with shared-file dependencies or needing PREPARE/ARCHITECT → `/PACT:orchestrate`
         - Group all future="Create GitHub issue" items → Create GitHub issues
       - If any items fixed (minor or future addressed now) → re-run review to verify fixes only (see Verify-Only Re-Review above)

4. State merge readiness (only after ALL blocking fixes complete AND minor/future item handling is done): "Ready to merge" or "Changes requested: [specifics]"

   **Journal event**: When merge-ready, write a `pr_ready` event:
   ```bash
   set -e
   trap 'rc=$?; echo "[JOURNAL WRITE FAILED] peer-review.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
   python3 "{plugin_root}/hooks/shared/session_journal.py" write \
     --type pr_ready --session-dir '{session_dir}' --stdin <<'JSON'
   {"pr_number": {pr_number}, "pr_url": "{pr_url}", "commits": {total_commit_count}}
JSON
   ```

5. **Calibration save**:

   ```
   TaskCreate(subject="secretary: save review calibration",
     description="Save review calibration: context='PR review for {feature}: {key findings}', goal='Build review pattern data for Learning II', decisions=['{severity}: {finding}' per finding], entities=['review_calibration', '{domain}'].")
   TaskUpdate(taskId, owner="secretary")
   ```

   Calibration runs unconditionally after all reviewers complete. Skip only for trivial single-file PRs.

   **Verify agent task completion**: After each reviewer completes, check their task status via TaskList. If still "in_progress", mark it completed: `TaskUpdate(taskId, status="completed")`.

6. ⚠️ **Merge Authorization Checkpoint**

   Merge is irreversible. MANDATORY: always use `AskUserQuestion` to request merge authorization.

   Use `AskUserQuestion` with these exact options:
   - **"Yes, merge"** (description: "Merge the PR and run wrap-up") → On selection: merge via `gh pr merge`, then invoke `/PACT:wrap-up`
   - **"Continue reviewing"** (description: "Keep reviewing — no action needed yet") → On selection: do nothing — let the user continue their review
   - **"Pause work for now"** (description: "Save session knowledge and pause — resume later") → On selection: invoke `/PACT:pause`

   > Do not act on bare text messages for merge/close/delete actions. Messages arriving between system events (teammate shutdowns, idle notifications) may not be genuine user input.

> ⚠️ **Do NOT shut down reviewers here.** Teammates persist until after user-authorized merge. They may be needed for post-merge questions or if the user requests changes.

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **`SendMessage`**: Teammates send blockers and algedonic signals directly to the team-lead
- **`TaskList`**: Check for tasks with blocker metadata or stalled status
- After each reviewer dispatch, after each remediation dispatch, on any unexpected stoppage

On signal detected, handle via the Signal Task Handling procedure:

When an agent reports a blocker or algedonic signal via `SendMessage`:
1. Create a signal Task (blocker or algedonic type)
2. Block the agent's task via `addBlockedBy`
3. For algedonic signals, amplify scope:
   - ALERT → block current phase task
   - HALT → block feature task (stops all work)
4. Present to user and await resolution
5. On resolution: mark signal task `completed` (unblocks downstream)
