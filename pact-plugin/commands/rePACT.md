---
description: Recursive nested PACT cycle for complex sub-tasks
argument-hint: [backend|frontend|database|prepare|test|architect|devops|security|qa] <sub-task description>
---
Run a recursive PACT cycle for this sub-task: $ARGUMENTS

This command initiates a **nested P→A→C→T cycle** for a sub-task that is too complex for simple delegation but should remain part of the current feature work.

**Team behavior**: rePACT spawns sub-scope teammates into the existing session team (`pact-{session_hash}`). No new team is created. Use scope-prefixed names (e.g., `backend-coder-auth-scope`) to distinguish sub-scope teammates from parent-scope teammates.

---

## Task Hierarchy

Create a nested Task hierarchy as a child of the current context:

```
1. `TaskCreate`: Sub-feature task "{verb} {sub-feature}" (child of parent context)
2. `TaskCreate`: Nested phase tasks:
   - "PREPARE: {sub-feature-slug}"
   - "ARCHITECT: {sub-feature-slug}"
   - "CODE: {sub-feature-slug}"
   - "TEST: {sub-feature-slug}"
3. `TaskUpdate`: Set dependencies:
   - Phase-to-phase blockedBy chain (same as orchestrate)
   - Parent task addBlockedBy = [sub-feature task]
4. `TaskUpdate`: Sub-feature task status = "in_progress"
5. Execute nested P→A→C→T cycle (same per-phase lifecycle as orchestrate: create phase task → `in_progress` → dispatch agents → agent tasks `in_progress` → `completed` → phase `completed`)
6. On completion: Parent task unblocked
```

**Example structure (standard):**
```
[Feature] "Implement user auth" (parent, blockedBy: sub-feature)
└── [Sub-Feature] "Implement OAuth2 token refresh"
    ├── [Phase] "PREPARE: oauth2-token-refresh"
    ├── [Phase] "ARCHITECT: oauth2-token-refresh"
    ├── [Phase] "CODE: oauth2-token-refresh"
    └── [Phase] "TEST: oauth2-token-refresh"
```

**Scope-aware naming** (when scope contract is provided):

When a scope contract provides a `scope_id`, prefix all tasks with `[scope:{scope_id}]`:

```
[Feature] "Implement user auth" (parent, blockedBy: sub-feature)
└── [Sub-Feature] "[scope:backend-api] Implement backend API"
    ├── [Phase] "[scope:backend-api] PREPARE: backend-api"
    ├── [Phase] "[scope:backend-api] ARCHITECT: backend-api"
    ├── [Phase] "[scope:backend-api] CODE: backend-api"
    └── [Phase] "[scope:backend-api] TEST: backend-api"
```

Include `scope_id` in task metadata: `{ "scope_id": "backend-api" }`. This enables the parent orchestrator to filter tasks by scope when aggregating results.

---

## When to Use rePACT

Use `/PACT:rePACT` when:
- A sub-task needs full P→A→C→T treatment (prepare, architect, code, test)
- The sub-task should stay on the current branch (no new branch/PR)
- You're already within a `/PACT:orchestrate` workflow

**Don't use rePACT when:**
- Sub-task is simple → use `/PACT:comPACT` instead
- Sub-task is a top-level feature → use `/PACT:orchestrate` instead
- You're not in an active orchestration → use `/PACT:orchestrate` instead

---

## Usage Modes

### Single-Domain Nested Cycle

When the sub-task fits within one specialist's domain:

```
/PACT:rePACT backend "implement OAuth2 token refresh mechanism"
```

This runs:
1. **Mini-Prepare**: Backend-focused research (token refresh best practices)
2. **Mini-Architect**: Backend component design (token storage, refresh flow)
3. **Mini-Code**: Backend implementation
4. **Mini-Test**: Smoke tests for the sub-component

### Multi-Domain Nested Cycle

When the sub-task spans multiple specialist domains:

```
/PACT:rePACT "implement payment processing sub-system"
```

This runs a mini-orchestration:
1. **Assess scope**: Determine which specialists are needed
2. **Mini-Prepare**: Research across relevant domains
3. **Mini-Architect**: Design the sub-system
4. **Mini-Code**: Invoke relevant coders (may be parallel)
5. **Mini-Test**: Smoke tests for the sub-system

---

## Specialist Selection

| Shorthand | Specialist | Use For |
|-----------|------------|---------|
| `backend` | pact-backend-coder | Server-side sub-components |
| `frontend` | pact-frontend-coder | UI sub-components |
| `database` | pact-database-engineer | Data layer sub-components |
| `prepare` | pact-preparer | Research-only nested cycles |
| `test` | pact-test-engineer | Test infrastructure sub-tasks |
| `architect` | pact-architect | Design-only nested cycles |
| `devops` | pact-devops-engineer | Infrastructure sub-components |
| `security` | pact-security-engineer | Security review nested cycles |
| `qa` | pact-qa-engineer | Runtime verification sub-tasks |

**If no specialist specified**: Assess the sub-task and determine which specialists are needed (multi-domain mode).

---

## Constraints

### Nesting Depth

**Maximum nesting: 1 level**

```
/PACT:orchestrate (level 0)
  └── /PACT:rePACT (level 1, max)
        └── /PACT:rePACT ← NOT ALLOWED
```

> **Design rationale**: V3 repurposed rePACT as the single-level executor for sub-scopes dispatched by ATOMIZE. Level 2 nesting is unreachable by design -- scope detection is bypassed within sub-scopes, so a sub-scope cannot trigger further decomposition.

If you hit the nesting limit:
- Simplify the sub-task and use `/PACT:comPACT`
- Or escalate to user for guidance

---

## Output Conciseness

See also: [Communication Charter](../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** User sees nested cycle start/completion, not mini-phase details.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Mini-phase transitions | `rePACT: backend "OAuth2 token refresh"` |
| Nesting depth calculations | `rePACT complete. Continuing parent.` |
| Phase skip reasoning | (implicit — just proceed) |

**User can always ask** for nested cycle details (e.g., "What phases ran?" or "Show me the mini-architect output").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Starting mini-PREPARE phase for the nested cycle..." | (just do it) |
| "The nested cycle has completed successfully..." | `rePACT complete. Continuing parent.` |

**Multi-scope aggregation**: When the parent orchestrator runs multiple rePACT sub-scopes, each sub-scope's handoff feeds into parent-level aggregation. The sub-scope should keep its handoff self-contained (no references to sibling state). The parent orchestrator is responsible for comparing fulfillment sections across sub-scopes during the consolidate phase.

---

### Branch Behavior

Branch behavior depends on whether rePACT is invoked with a scope contract:

**Without scope contract** (standard nested cycle):
- **No new branch**: rePACT stays on the current feature branch
- **No PR**: Results integrate into the parent task's eventual PR
- All commits remain part of the current feature work

**With scope contract** (from ATOMIZE phase):
- **Receives worktree path** from the parent orchestrator (created by parent via `/PACT:worktree-setup`)
- **Operates in isolated worktree** on a suffix branch (e.g., `feature-X--{scope_id}`)
- **Pass worktree path to all agent prompts**: Include "You are working in a git worktree at [worktree_path]. Note: `CLAUDE.md` is gitignored and does not exist in worktrees. Do NOT edit or create `CLAUDE.md` — the orchestrator manages it separately. If your task mentions updating `CLAUDE.md`, flag it in your handoff instead." in specialist dispatches, consistent with orchestrate.md
- All commits stay on the suffix branch within the worktree
- Branch merges back to the feature branch during the CONSOLIDATE phase

---

## Workflow

### Phase 0: Assess

Before starting, verify:
1. **Nesting depth**: Read `TaskGet(taskId).metadata.nesting_depth` — if > 1, reject (max depth exceeded). If absent, treat as 0.
2. **Scope contract**: If this rePACT was dispatched from ATOMIZE, read the scope contract from `TaskGet(taskId).metadata.scope_contract` instead of expecting it inline in the prompt. This ensures scope state survives compaction.
3. **Scope appropriateness**: Is this truly a sub-task of the parent?
4. **Domain determination**: Single-domain or multi-domain?

### Phase 1: Mini-Prepare (if needed)

For the sub-task, gather focused context:
- Research specific to the sub-component
- May be skipped if parent Prepare phase covered this
- Output: Notes integrated into parent preparation or separate `-nested` doc

### Phase 2: Mini-Architect (if needed)

Design the sub-component:
- Component design within the larger architecture
- Interface contracts with parent components
- May be skipped for simple sub-tasks
- Output: Design notes in `-nested` architecture doc or inline

### Phase 3: Mini-Code

Implement the sub-component:

**Verify session team exists**: The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.

> **Nesting note**: if `/PACT:rePACT` is invoked nested within `/PACT:orchestrate` (or any other parent workflow that already armed the wake mechanism), the parent's wake-arming applies — do NOT re-arm. Detect by checking `~/.claude/teams/{team_name}/inbox-wake-state.json`. Three cases:
> - **STATE_FILE present + HB fresh (current_epoch − HB_FILE.ts < 420)**: parent is alive and emitting heartbeats. Skip both Monitor and Cron arm; proceed to the dispatch sections below.
> - **STATE_FILE present + HB stale (current_epoch − HB_FILE.ts >= 420)**: do NOT re-arm here — the parent's cron Branch C will detect staleness and re-arm on its next fire (≤4min). Skip both Monitor and Cron arm; proceed to the dispatch sections below.
> - **STATE_FILE missing**: no parent armed (or teardown already ran); fall through to the canonical Monitor + Cron arm steps below as if rePACT were a top-level invocation.

**Arm inbox-wake mechanism (case-3 fallthrough only)**: If you fell through to case 3 of the nesting note above (STATE_FILE missing): run the canonical Monitor block, run the canonical Cron block, then write the registry state file. Capture `Monitor` task_id as `M_ID` and `CronCreate` cron_job_id as `C_ID`.

## Inbox Wake — Arm Monitor (start)

```markdown
Monitor(
  description="pact-inbox-monitor:{team_name}:team-lead",
  command="""
    TEAM_DIR="$HOME/.claude/teams/{team_name}"
    INBOX="$TEAM_DIR/inboxes/team-lead.json"
    HB_FILE="$TEAM_DIR/inbox-wake-heartbeat.json"
    HB_TMP="$TEAM_DIR/inbox-wake-heartbeat.json.tmp"
    LAST_COUNT=-1
    HB_INTERVAL=300
    HB_LAST=0
    while true; do
      NOW=$(date +%s)
      if [ -f "$INBOX" ]; then
        COUNT=$(jq 'length' "$INBOX" 2>/dev/null || echo 0)
      else
        COUNT=0
      fi
      if [ "$LAST_COUNT" -eq -1 ] && [ "$COUNT" -gt 0 ]; then
        echo "INBOX_GREW count=$COUNT prev=0 ts=$NOW reason=startup"
      fi
      if [ "$LAST_COUNT" -ge 0 ] && [ "$COUNT" -gt "$LAST_COUNT" ]; then
        echo "INBOX_GREW count=$COUNT prev=$LAST_COUNT ts=$NOW"
      fi
      LAST_COUNT=$COUNT
      printf '{"v":1,"count":%d,"ts":%d}\n' "$COUNT" "$NOW" > "$HB_TMP" && mv -f "$HB_TMP" "$HB_FILE"
      if [ $((NOW - HB_LAST)) -ge $HB_INTERVAL ]; then
        echo "HEARTBEAT count=$COUNT ts=$NOW"
        HB_LAST=$NOW
      fi
      sleep 5
    done
  """,
  persistent=True
)
```

## Inbox Wake — Arm Monitor (end)

## Inbox Wake — Write State File (start)

After `Monitor` returns `M_ID`, write the registry state file (atomic-rename) so the cron's recovery rule can read it:

```bash
STATE_FILE="$HOME/.claude/teams/{team_name}/inbox-wake-state.json"
STATE_TMP="$STATE_FILE.tmp"
printf '{"v":1,"monitor_task_id":"%s","cron_job_id":"%s","armed_at":%d}\n' "$M_ID" "$C_ID" "$(date +%s)" > "$STATE_TMP" && mv -f "$STATE_TMP" "$STATE_FILE"
```

`C_ID` is captured from the `CronCreate` call below — write the state file AFTER both `Monitor` and `CronCreate` have returned successfully. If either fails, do NOT write the state file (the recovery rule's Branch A cold-start will re-arm on the next cron-fire).

## Inbox Wake — Write State File (end)

## Inbox Wake — Arm Cron (start)

```markdown
CronCreate(
  description="pact-inbox-cron:{team_name}:team-lead",
  schedule="*/4 * * * *",
  durable=False,
  recurring=True,
  prompt="""
PACT inbox-wake recovery check. Run the following AS the lead, this turn:

1. Read the registry sidecar files:
   - STATE_FILE: ~/.claude/teams/{team_name}/inbox-wake-state.json
   - HB_FILE: ~/.claude/teams/{team_name}/inbox-wake-heartbeat.json

2. BRANCH on file presence + heartbeat freshness:

   Branch A — STATE_FILE missing → COLD START:
     a. Re-run the canonical block under this workflow's command file's "Inbox Wake — Arm Monitor" H2 section. Capture the returned task_id as M_ID.
     b. Re-run THIS workflow's command file's "Inbox Wake — Arm Cron" H2 section to re-arm the cron. Capture the returned cron_job_id as C_ID. (Note: the recovery check itself is fired BY the cron; if STATE_FILE is missing, we are in cold-start, so a re-arm of the cron is the correct response — the prior cron may have been from a stale session. CronCreate is idempotent under deterministic-naming + per-session in-memory CronList scope. If you cannot determine which workflow file is "this" at cron-fire time, default to orchestrate.md's canonical blocks for both steps (a) and (b); the canonical Monitor and Cron blocks are byte-equivalent across all 5 ARMING_FILES per scripts/verify-protocol-extracts.sh, so any callsite is correct.)
     c. Write STATE_FILE with: {"v":1,"monitor_task_id":"<M_ID>","cron_job_id":"<C_ID>","armed_at":<current_epoch>}. Use atomic-rename via *.tmp + mv. DONE.

   Branch B — STATE_FILE present + HB_FILE present + HB_FILE.ts is fresh (current_epoch - ts < 420):
     No-op. The Monitor is alive and emitting heartbeats. DONE.

   Branch C — STATE_FILE present + (HB_FILE missing OR HB_FILE.ts is stale (current_epoch - ts >= 420)):
     a. Read STATE_FILE.monitor_task_id as M_ID_OLD. TaskStop(M_ID_OLD). On benign error (task already stopped / not found), continue.
     b. Unlink HB_FILE if present. Unlink STATE_FILE.
     c. Goto Branch A (cold-start the Monitor; cron does NOT need re-arming because the cron-fire that triggered THIS rule proves the cron is alive). (Branch A step (c) writes a fresh STATE_FILE.)

3. FAIL-OPEN: if any file Read errors (malformed JSON, schema mismatch v != 1, etc.), treat as Branch C (stop+unlink+re-arm). If any TaskStop / Monitor / CronCreate / atomic-rename errors, log to stdout for the cron-fire turn and continue with the remaining branch steps. Cost asymmetry: false-arm = one extra cache-warm fire; false-skip = unbounded blind window.

This rule is FROZEN. Do NOT modify behavior beyond what is written above. Do NOT escalate to user unless TaskStop returns a non-benign error (a benign error is "task already stopped" or "task not found").
"""
)
```

## Inbox Wake — Arm Cron (end)

**Two-Task Dispatch Shape (TEACHBACK + WORK)**

Each specialist dispatch creates **two tasks**, not one:

- **Task A** — TEACHBACK gate. `subject = "{scope-prefixed-name}: TEACHBACK for {sub-task}"`, owner = specialist.
- **Task B** — primary work. `subject = "{scope-prefixed-name}: implement {sub-task}"`, owner = specialist, `blockedBy = [<Task A id>]`.

Both are created BEFORE the `Task(...)` spawn call. The specialist claims A, submits teachback metadata, idles on `awaiting_lead_completion`. You review and accept via the two-call atomic pair (`TaskUpdate(A, status="completed")` + paired wake-signal SendMessage — see [orchestration §Teachback Review](../skills/orchestration/SKILL.md#teachback-review)). On accept, the specialist wakes to claim B.

Nested PACT cycles' inner-cycle dispatches follow the same A+B shape recursively. The `Task()` `prompt` does NOT change shape.

```
A_id = TaskCreate(
    subject="{scope-prefixed-name}: TEACHBACK for {sub-task}",
    description="DOGFOOD TEACHBACK GATE.\n\n"
                "Submit teachback by writing metadata.teachback_submit (per pact-teachback skill). "
                "SET intentional_wait{reason=awaiting_lead_completion}. Idle. "
                "DO NOT mark this task completed — team-lead-only completion.\n\n"
                "Mission for Task B: see Task #{B_id}."
)
TaskUpdate(A_id, owner="{scope-prefixed-name}")
B_id = TaskCreate(subject="{scope-prefixed-name}: implement {sub-task}", description="<full mission>")
TaskUpdate(B_id, owner="{scope-prefixed-name}", addBlockedBy=[A_id])
TaskUpdate(A_id, addBlocks=[B_id])
```

---

For each specialist needed — apply the shape above:

1. `TaskCreate(subject="{scope-prefixed-name}: implement {sub-task}", description="[full CONTEXT/MISSION/INSTRUCTIONS/GUIDELINES]")`
2. `TaskUpdate(taskId, owner="{scope-prefixed-name}")`
3. Spawn the specialist with the canonical dispatch form. The `prompt` MUST lead with the `YOUR PACT ROLE: teammate ({scope-prefixed-name})` marker on its own line and include the `Skill("PACT:teammate-bootstrap")` YOUR FIRST ACTION directive:

```
Task(
  name="{scope-prefixed-name}",
  team_name="{team_name}",
  subagent_type="pact-{specialist-type}",
  prompt="YOUR PACT ROLE: teammate ({scope-prefixed-name}).\n\nYOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY): invoke Skill(\"PACT:teammate-bootstrap\"). This loads the team communication protocol, teachback standards, memory retrieval, and algedonic reference. If your context is later compacted and you find yourself without this content loaded, re-invoke the skill before continuing implementation.\n\nYou are joining team {team_name}. Check `TaskList` for tasks assigned to you."
)
```

> ⚠️ **`{scope-prefixed-name}` constraint (SECURITY)**: the `name=` value is interpolated verbatim into the `YOUR PACT ROLE: teammate ({scope-prefixed-name}).` marker line. `name` MUST match `^[a-z0-9-]+$` — lowercase alphanumerics and hyphens only, no spaces, no newlines, no parentheses — to prevent marker spoofing. The `peer_inject.py` hook also sanitizes defensively by stripping `\n`, `\r`, and `)` as a second layer of defense.

For multi-domain: spawn multiple specialists in parallel.
Apply S2 coordination if parallel work.
Output: Code + HANDOFF in task metadata (summary via `SendMessage` to team-lead).

### Phase 4: Mini-Test

Verify the sub-component:
- Smoke tests for the sub-component
- Verify integration with parent components
- Output: Test results in handoff

### Phase 5: Integration

Complete the nested cycle:
1. **Verify**: Sub-component works within parent context
2. **Handoff**: Return control to parent orchestration with summary
3. **Agreement verification**: Parent orchestrator verifies understanding of nested results before reintegrating into parent scope.
   - `SendMessage` to each contributing specialist to confirm: "Confirming my understanding of the nested results: [restates key deliverables and decisions]. Correct?"
   - Background: [pact-ct-teachback.md](../protocols/pact-ct-teachback.md).

---

## Context Inheritance

Nested cycles inherit from parent:
- Current feature branch
- Parent task context and requirements
- Architectural decisions from parent
- Coding conventions established in parent

Nested cycles produce:
- Code committed to current branch
- Handoff summary for parent orchestration

**CT note**: Nested cycles are *conversations within conversations*. The parent scope's understanding provides context for the nested conversation. When the nested conversation completes, its understanding must be verified before reintegrating into the parent. Agreement verification at nested boundaries follows the same mechanism as phase boundaries in orchestrate. Background: [pact-ct-teachback.md](../protocols/pact-ct-teachback.md).

---

## Scope Contract Reception

When the parent orchestrator invokes rePACT with a **scope contract** (from scope detection and decomposition), the nested cycle operates scope-aware. Without a contract, rePACT behaves as described above. Contract presence is the mode switch — there are no explicit "modes" to select.

**When a scope contract is provided:**

1. **Identity**: Use the contract's `scope_id` as the scope identifier for all task naming and metadata (see Task Hierarchy above)
2. **Deliverables**: Treat contracted deliverables as the success criteria for Mini-Code and Mini-Test
3. **Interfaces**: Use `imports` to understand what sibling scopes provide; use `exports` to ensure this scope exposes what siblings expect
4. **Shared files constraint**: Do NOT modify files listed in the contract's `shared_files` — these are owned by sibling scopes. Communicate this constraint to all dispatched specialists.
5. **Conventions**: Apply any `conventions` from the contract in addition to inherited parent conventions
6. **Handoff**: Include a Contract Fulfillment section in the completion handoff (see After Completion below)

**When no scope contract is provided:** Standard rePACT behavior. No scope-aware naming, no contract fulfillment tracking, no shared file constraints.

See [pact-scope-contract.md](../protocols/pact-scope-contract.md) for the contract format specification.

---

## Relationship to Specialist Autonomy

Specialists can invoke nested cycles autonomously (see [Autonomy Charter](../protocols/pact-s1-autonomy.md#autonomy-charter)).
`/PACT:rePACT` is for **orchestrator-initiated** nested cycles.

| Initiator | Mechanism |
|-----------|-----------|
| Specialist discovers complexity | Uses Autonomy Charter (declares, executes, reports) |
| Orchestrator identifies complex sub-task | Uses `/PACT:rePACT` command |

Both follow the same protocol; the difference is who initiates.

---

## Examples

### Example 1: Single-Domain Backend Sub-Task

```
/PACT:rePACT backend "implement rate limiting middleware"
```

Orchestrator runs mini-cycle:
- Mini-Prepare: Research rate limiting patterns
- Mini-Architect: Design middleware structure
- Mini-Code: Invoke backend coder
- Mini-Test: Smoke test rate limiting

### Example 2: Multi-Domain Sub-System

```
/PACT:rePACT "implement audit logging sub-system"
```

Orchestrator assesses scope:
- Needs: backend (logging service), database (audit tables), frontend (audit viewer)
- Runs mini-orchestration with all three domains
- Coordinates via S2 protocols

### Example 3: Skipping Phases

```
/PACT:rePACT frontend "implement form validation component"
```

If parent already has:
- Validation requirements (skip mini-prepare)
- Component design (skip mini-architect)

Then just run mini-code and mini-test.

---

## Error Handling

**If nesting limit exceeded:**
```
⚠️ NESTING LIMIT: Cannot invoke rePACT at level 2.
Options:
1. Simplify sub-task and use comPACT
2. Escalate to user for guidance
```

**If sub-task is actually top-level:**
```
⚠️ SCOPE MISMATCH: This appears to be a top-level feature, not a sub-task.
Consider using /PACT:orchestrate instead.
```

---

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **`SendMessage`**: Teammates send blockers and algedonic signals directly to the team-lead
- **`TaskList`**: Check for tasks with blocker metadata or stalled status

On signal detected, handle via the Signal Task Handling procedure:

When an agent reports a blocker or algedonic signal via `SendMessage`:
1. Create a signal Task (blocker or algedonic type)
2. Block the agent's task via `addBlockedBy`
3. For algedonic signals, amplify scope:
   - ALERT → block current phase task
   - HALT → block feature task (stops all work)
4. Present to user and await resolution
5. On resolution: mark signal task `completed` (unblocks downstream)

For agent stall detection and recovery, see [Agent Stall Detection](orchestrate.md#agent-stall-detection).

---

## After Completion

When nested cycle completes:
1. **`TaskUpdate`**: Sub-feature task status = "completed"
2. **Summarize** what was done in the nested cycle
3. **Report** any decisions that affect the parent task
4. **Continue** with parent orchestration (parent task now unblocked)

**Handoff format**: Use the standard handoff structure (Produced, Key decisions, Reasoning chain [recommended], Areas of uncertainty, Integration points, Open questions — 5 required fields, 1 recommended).

**Contract-aware handoff** (when scope contract was provided): Append a Contract Fulfillment section after the standard handoff:

```
Contract Fulfillment:
  Deliverables:
    - ✅ {delivered item} → {actual file/artifact}
    - ❌ {undelivered item} → {reason}
  Interfaces:
    exports: {what was actually exposed}
    imports: {what was actually consumed from siblings}
  Deviations: {any departures from the contract, with rationale}
```

The parent orchestrator uses fulfillment sections from all sub-scopes to drive the consolidate phase. Keep the fulfillment section factual and concise — the parent only needs to know what matched, what didn't, and why.

The parent orchestration resumes with the sub-task complete.
