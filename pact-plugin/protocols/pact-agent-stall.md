## Agent Stall Detection

**Stalled indicators** (Agent Teams model):
- TeammateIdle event received but no completion message or blocker was sent via `SendMessage`
- Task status in `TaskList` shows `in_progress` but no `SendMessage` activity from the teammate
- Teammate process terminated without sending a completion message or blocker via `SendMessage`

Detection is event-driven: check at signal monitoring points (after dispatch, on TeammateIdle events, on `SendMessage` receipt). If a teammate goes idle without sending a completion message or blocker, treat as stalled immediately.

### Recovery Protocol

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

**Convention**: All non-happy-path terminations use `completed` with metadata — no `failed` status exists. This preserves the `pending → in_progress → completed` lifecycle.

---
