## Agent Stall Detection and Idle Cleanup

The `teammate_idle.py` hook handles two responsibilities triggered by the `TeammateIdle` event:

1. **Stall detection** — Identifies teammates that went idle with an `in_progress` task (no completion or blocker sent)
2. **Idle cleanup** — Tracks consecutive idle events for completed teammates and suggests or forces shutdown

**Relationship to agent state model**: Stall detection is the binary endpoint (active vs. stalled). For finer-grained mid-execution assessment (converging/exploring/stuck), see the agent state model in [pact-variety.md](pact-variety.md#agent-state-model). An agent assessed as "stuck" via progress signals may stall if not intervened upon.

---

### Hook Interface

**Trigger**: `TeammateIdle` event (fires when a teammate goes idle)

**Input** (JSON on stdin):
```json
{
  "teammate_name": "backend-coder-1",
  "team_name": "pact-abc123"
}
```

**Output** (JSON on stdout, when action needed):
```json
{
  "systemMessage": "[System idle notification — no response needed] <message>"
}
```

The `systemMessage` is prefixed with an idle preamble to signal the orchestrator that no direct response is required. All messages within a single invocation are joined with ` | `.

**Exit behavior**: The hook exits 0 in all cases (including errors) to avoid blocking the orchestrator. Errors are logged to stderr.

---

### Stall Detection

A stall is detected when ALL of the following are true:
- The teammate has a task with `status: "in_progress"`
- The `TeammateIdle` event fired (teammate went idle without completing)
- The task is NOT a signal task (metadata `type` is not `"blocker"` or `"algedonic"`)
- The task is NOT already marked as stalled (metadata `stalled` is not true)

**Task lookup**: The hook finds the teammate's task by matching `owner` field. If both `in_progress` and `completed` tasks exist for the same owner, `in_progress` takes priority. Among multiple completed tasks, the highest task ID (most recent) is selected.

**Stall output**: When a stall is detected, the hook emits:
```
Teammate '{name}' went idle without completing task #{id} ({subject}). Possible stall. Consider /PACT:imPACT to triage.
```

**When stall is detected, idle cleanup is skipped** — stalled agents need triage, not shutdown.

---

### Idle Cleanup

Idle cleanup tracks consecutive idle events for teammates whose task is `completed` (not stalled or terminated). It suggests shutdown after repeated idles to free resources.

#### Thresholds

| Constant | Default | Behavior |
|----------|---------|----------|
| `IDLE_SUGGEST_THRESHOLD` | 3 | Emit suggestion to shut down the teammate |
| `IDLE_FORCE_THRESHOLD` | 5 | Emit shutdown request instruction |

At the force threshold, the hook appends an `ACTION REQUIRED` message instructing the orchestrator to send a `shutdown_request` via `SendMessage`. Hooks cannot call `SendMessage` directly — they output text instructions for the orchestrator.

#### State Storage

Idle counts are stored per-team at:
```
~/.claude/teams/{team_name}/idle_counts.json
```

The file maps teammate names to structured entries:
```json
{
  "backend-coder-1": {"count": 3, "task_id": "8"},
  "frontend-coder-1": {"count": 1, "task_id": "12"}
}
```

**Legacy migration**: Plain integer entries (from earlier versions) are migrated to the structured format on read.

#### Count Reset

The idle count resets to zero when:
- The teammate's task changes (different `task_id` than last seen) — indicates reassignment to new work
- The teammate no longer has a completed task (got new `in_progress` work, or task removed)

#### Exclusions

Idle cleanup does NOT count idles for:
- Teammates with no task
- Teammates with `in_progress` tasks (those go through stall detection instead)
- Teammates whose completed task has metadata `stalled: true` or `terminated: true` (these need triage, not shutdown)

---

### Concurrency Safety

Multiple `TeammateIdle` events can fire concurrently for different teammates. The hook uses atomic read-modify-write to prevent TOCTOU races on `idle_counts.json`.

**On platforms with `fcntl`** (macOS, Linux):
- Opens the file in append mode (`a+`) to avoid truncation before lock acquisition
- Acquires an exclusive lock (`fcntl.flock(LOCK_EX)`)
- Reads current content, applies mutation, truncates, writes updated content
- Releases lock in a `finally` block

**On platforms without `fcntl`** (Windows):
- Falls back to non-atomic read + write (acceptable since concurrent hook invocations are unlikely on Windows)

Both `write_idle_counts` (standalone write) and `_atomic_update_idle_counts` (read-modify-write) use this locking pattern. The `_atomic_update_idle_counts` function takes a `mutator` callable that receives the current counts dict and returns the updated dict, ensuring the entire read-modify-write cycle happens under a single lock.

---

### Recovery Protocol

When the orchestrator receives a stall alert:

1. Check the teammate's `TaskList` status and any partial task metadata or `SendMessage` output for context on what happened
2. Mark the stalled agent task as `completed` with `metadata={"stalled": true, "reason": "{what happened}"}`
3. Assess: Is the work partially done? Can it be continued from where it stopped?
4. Create a new agent task and spawn a new teammate to retry or continue the work, passing any partial output as context
5. If stall persists after 1 retry, emit an **ALERT** algedonic signal (META-BLOCK category)

### Prevention

Include in agent prompts: "If you encounter an error that prevents completion, send a message via `SendMessage` describing what you completed and store a partial HANDOFF in task metadata rather than silently failing."

### Non-Happy-Path Task Termination

When an agent cannot complete normally (stall, failure, or unresolvable blocker), mark its task as `completed` with descriptive metadata:

Metadata: `{"stalled": true, "reason": "..."}` | `{"failed": true, "reason": "..."}` | `{"blocked": true, "blocker_task": "..."}`

**Convention**: All non-happy-path terminations use `completed` with metadata — no `failed` status exists. This preserves the `pending -> in_progress -> completed` lifecycle.

---
