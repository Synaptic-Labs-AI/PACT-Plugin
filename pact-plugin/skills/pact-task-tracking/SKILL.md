---
name: pact-task-tracking
description: |
  Task tracking and communication protocol for PACT specialist agents.
  Defines how to read tasks, report progress, store handoffs, and communicate
  via SendMessage in an Agent Teams environment.
---

# Task Tracking & Communication Protocol

> **Architecture**: See [pact-task-hierarchy.md](../../protocols/pact-task-hierarchy.md) for the full hierarchy model.

## On Start — Read Your Task

You have access to Task tools (TaskGet, TaskUpdate, TaskList). On start:

1. **TaskGet** on your assigned task ID to read your full mission (description, metadata, constraints)
2. Check `metadata.upstream_tasks` — if present, **TaskGet** each to read upstream handoff data
3. Check `metadata.artifact_paths` — if present, read those files for content context
4. Check `metadata.coordination` — respect file boundaries, conventions, and concurrent agent notes
5. Begin work with full context from the task graph

This is the **chain-read pattern**: you get your context from the task graph, not from the dispatch prompt.

## Communication via SendMessage

Communicate with your assigner (whoever dispatched you) using SendMessage:

| Event | Action |
|-------|--------|
| **Task complete** | SendMessage: "Task {ID} complete" (thin signal — details are in Task metadata) |
| **Blocker hit** | SendMessage: "BLOCKER on task {ID}: {one-line description}" |
| **Algedonic signal** | SendMessage (or broadcast for HALT): formatted signal per algedonic protocol |
| **Peer coordination** | SendMessage to specific teammate (sparingly, within-phase only) |

**SendMessage carries no content.** It is a thin signal — it tells the recipient *something happened*. The recipient reads your Task metadata to learn details. Do NOT send handoff summaries, file contents, or detailed status via SendMessage.

### HALT Broadcasts

For HALT-level algedonic signals (SECURITY, DATA, ETHICS), use **broadcast** instead of direct message. This simultaneously notifies ALL teammates to stop work:

```
SendMessage(type: "broadcast", content: "⚠️ ALGEDONIC HALT: {Category} — {one-line issue}")
```

On receiving a HALT broadcast from another teammate: stop work immediately. Approve any pending shutdown request.

## Storing Your Handoff

When your work is complete, store your handoff in Task metadata via TaskUpdate:

```
TaskUpdate(taskId, metadata={
  "handoff": {
    "produced": ["path/to/file1.ts", "path/to/file2.ts"],
    "decisions": [{"decision": "...", "rationale": "..."}],
    "uncertainties": [{"level": "HIGH", "description": "..."}],
    "integration_points": ["ComponentA.method()"],
    "open_questions": ["Rate limiting strategy TBD"]
  }
})
```

All five handoff fields are required. Then send a thin completion signal:

```
SendMessage(type: "message", recipient: "{assigner}", content: "Task {ID} complete", summary: "Task {ID} complete")
```

## On Blocker

If you cannot proceed:

1. **Stop work immediately**
2. Store partial handoff in Task metadata (whatever you completed)
3. Send blocker signal: `SendMessage(type: "message", recipient: "{assigner}", content: "BLOCKER on task {ID}: {description}", summary: "Blocker on task {ID}")`

Do not attempt to work around the blocker. Your assigner will triage and resolve it.

## On Algedonic Signal

When you detect a viability threat (security, data integrity, ethics):

1. **Stop work immediately**
2. For **HALT**: Broadcast to all — `SendMessage(type: "broadcast", content: "⚠️ ALGEDONIC HALT: ...")`
3. For **ALERT**: Message your assigner — `SendMessage(type: "message", recipient: "{assigner}", content: "⚠️ ALGEDONIC ALERT: ...")`
4. Store partial handoff in Task metadata

See the algedonic protocol for trigger categories and severity guidance.

## Peer Communication

You may message other teammates directly for within-phase coordination (e.g., clarifying interface contracts with a concurrent specialist). Use sparingly:

```
SendMessage(type: "message", recipient: "{teammate-name}", content: "...", summary: "...")
```

Discover teammates by reading the team config file at `~/.claude/teams/{team-name}/config.json`.

## Shutdown Protocol

When you receive a `shutdown_request`:
- If you have completed your work: approve the shutdown
- If you are mid-task: store partial handoff, then approve
- On receiving a HALT broadcast: stop work immediately, approve any pending shutdown
