## Agent Stall Detection and Idle Cleanup

The `teammate_idle.py` hook monitors teammates for two conditions:

1. **Stall detection** — A teammate went idle with an `in_progress` task (no completion or blocker sent)
2. **Idle cleanup** — A teammate with a completed task has been idle too long; suggests or forces shutdown

**Relationship to agent state model**: Stall detection is the binary endpoint (active vs. stalled). For finer-grained mid-execution assessment (converging/exploring/stuck), see the agent state model in [pact-variety.md](pact-variety.md#agent-state-model). An agent assessed as "stuck" via progress signals may stall if not intervened upon.

---

### Stall Detection

A stall is detected when a teammate goes idle while owning an `in_progress` task that is not a signal task (blocker/algedonic) and not already marked stalled. The orchestrator receives:

```
Teammate '{name}' went idle without completing task #{id} ({subject}). Possible stall. Consider /PACT:imPACT to triage.
```

---

### Idle Cleanup Thresholds

| Threshold | Default | Behavior |
|-----------|---------|----------|
| `IDLE_SUGGEST_THRESHOLD` | 3 | Suggests shutting down the idle teammate |
| `IDLE_FORCE_THRESHOLD` | 5 | Emits `ACTION REQUIRED` — orchestrator should send `shutdown_request` via `SendMessage` |

Idle counts reset when the teammate gets new work (different task ID) or their task status changes.

---

### Recovery Protocol

When the orchestrator receives a stall alert:

1. Check the teammate's `TaskList` status and any partial `SendMessage` output for context
2. Mark the stalled task as `completed` with `metadata={"stalled": true, "reason": "{what happened}"}`
3. Assess: Is the work partially done? Can it be continued?
4. Spawn a new teammate to retry or continue, passing any partial output as context
5. If stall persists after 1 retry, emit an **ALERT** algedonic signal (META-BLOCK category)

### Prevention

Include in agent prompts: "If you encounter an error that prevents completion, send a message via `SendMessage` describing what you completed and store a partial HANDOFF in task metadata rather than silently failing."

### Non-Happy-Path Task Termination

When an agent cannot complete normally, mark its task as `completed` with descriptive metadata:

| Situation | Metadata |
|-----------|----------|
| Stall | `{"stalled": true, "reason": "..."}` |
| Failure | `{"failed": true, "reason": "..."}` |
| Blocked | `{"blocked": true, "blocker_task": "..."}` |

**Convention**: All non-happy-path terminations use `completed` with metadata — no `failed` status exists. This preserves the `pending → in_progress → completed` lifecycle.

---
