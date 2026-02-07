## Teammate Stall Detection

**Stalled indicators**:
- No SendMessage received from teammate for an extended period after spawning
- Plan approval sent but no plan received from teammate (stuck in plan mode)
- Task status not updating (teammate claimed task via TaskUpdate but no further progress)
- Teammate process terminated without HANDOFF or BLOCKER message

Detection is event-driven: check at monitoring points (after spawning, periodically during execution, on expected completion). If a teammate has been spawned but produced no SendMessage (HANDOFF, BLOCKER, or progress update) within a reasonable timeframe, treat as potentially stalled.

### Recovery Protocol

1. Send a status inquiry message to the teammate: `SendMessage(type: "message", recipient: "{teammate-name}", content: "Status check: Please report your current progress.", summary: "Status inquiry")`
2. Wait for a response. Teammates complete their in-progress task before processing inbox messages, so allow reasonable time.
3. If no response received, the teammate may have terminated. Mark the stalled task as `completed` with `metadata={"stalled": true, "reason": "{what happened}"}`
4. Spawn a new teammate to retry or continue the work, passing any partial output as context
5. If stall persists after 1 retry, emit an **ALERT** algedonic signal (META-BLOCK category)

### Prevention

Include in teammate spawn prompts: "If you encounter an error that prevents completion, send a partial HANDOFF via SendMessage to team-lead with whatever work you completed rather than silently failing."

### Non-Happy-Path Task Termination

When a teammate cannot complete normally (stall, failure, or unresolvable blocker), the lead marks its task as `completed` with descriptive metadata:

Metadata: `{"stalled": true, "reason": "..."}` | `{"failed": true, "reason": "..."}` | `{"blocked": true, "blocker_task": "..."}`

**Convention**: All non-happy-path terminations use `completed` with metadata — no `failed` status exists. This preserves the `pending → in_progress → completed` lifecycle.

---
