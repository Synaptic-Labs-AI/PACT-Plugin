## Agent Stall Detection

**Stalled indicators**:
- Teammate idle with no handoff delivered (detected via TeammateIdle hook)
- Task stuck in `in_progress` with no progress at monitoring checkpoints
- Teammate process terminated without handoff or blocker report

Detection is event-driven via two mechanisms:
1. **TeammateIdle hook**: Fires when a teammate stops working. If the teammate's task has no handoff, treat as stalled immediately.
2. **Lead monitoring**: Lead periodically checks TaskList for tasks stuck `in_progress` without recent updates (timeout-based detection).

### Recovery Protocol

1. Check the teammate's messages (via SendMessage history) for partial work or error reports
2. Mark the stalled agent task as `completed` with `metadata={"stalled": true, "reason": "{what happened}"}`
3. Assess: Is the work partially done? Can it be continued from where it stopped?
4. Spawn a new teammate to retry or continue the work, passing any partial output as context
5. If stall persists after 1 retry, emit an **ALERT** algedonic signal (META-BLOCK category)

### Prevention

Teammates are instructed via the pact-task-tracking skill: "If you encounter an error that prevents completion, report a partial handoff with whatever work you completed rather than silently failing."

### Non-Happy-Path Task Termination

When a teammate cannot complete normally (stall, failure, or unresolvable blocker), mark its task as `completed` with descriptive metadata:

Metadata: `{"stalled": true, "reason": "..."}` | `{"failed": true, "reason": "..."}` | `{"blocked": true, "blocker_task": "..."}`

**Convention**: All non-happy-path terminations use `completed` with metadata — no `failed` status exists. This preserves the `pending → in_progress → completed` lifecycle.

---
