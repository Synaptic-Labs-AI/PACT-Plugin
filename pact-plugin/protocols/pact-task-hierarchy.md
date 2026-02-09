## Task Hierarchy

This document explains how PACT uses Claude Code's Task system to track work at multiple levels.

### Hierarchy Levels

```
Feature Task (created by lead)
├── Phase Tasks (PREPARE, ARCHITECT, CODE, TEST)
│   ├── Agent Task 1 (specialist work)
│   ├── Agent Task 2 (parallel specialist)
│   └── Agent Task 3 (parallel specialist)
└── Review Task (peer-review phase)
```

### Task Ownership

| Level | Created By | Owned By | Lifecycle |
|-------|------------|----------|-----------|
| Feature | Lead | Lead | Spans entire workflow |
| Phase | Lead | Lead | Active during phase |
| Agent | Lead | Specialist | Completed when specialist finishes |

**Note**: Teammates have access to Task tools (TaskGet, TaskUpdate, TaskList) as a platform feature. They use these for self-management — updating their own task status and creating sub-tasks when needed. The lead creates the initial agent task; the specialist manages it from there.

### Task States

Tasks progress through: `pending` → `in_progress` → `completed`

- **pending**: Created but not started
- **in_progress**: Active work underway
- **completed**: Work finished (success or documented failure)

### Blocking Relationships

Use `addBlockedBy` to express dependencies:

```
CODE phase task
├── blockedBy: [ARCHITECT task ID]
└── Agent tasks within CODE
    └── blockedBy: [CODE phase task ID]
```

### Metadata Conventions

Agent tasks include metadata for context:

```json
{
  "phase": "CODE",
  "domain": "backend",
  "feature": "user-auth",
  "handoff": {
    "produced": ["src/auth.ts"],
    "uncertainty": ["token refresh edge cases"]
  }
}
```

### Scope-Aware Task Conventions

When decomposition creates sub-scopes, tasks use naming and metadata conventions to maintain scope ownership.

#### Naming Convention

Prefix task subjects with `[scope:{scope_id}]` to make TaskList output scannable:

```
[scope:backend-api] ARCHITECT: backend-api
[scope:backend-api] CODE: backend-api
[scope:frontend-ui] CODE: frontend-ui
```

Tasks without a scope prefix belong to the root (parent) lead scope.

#### Scope Metadata

Include `scope_id` in task metadata to enable structured filtering:

```json
{
  "scope_id": "backend-api",
  "phase": "CODE",
  "domain": "backend"
}
```

The lead iterates all tasks and filters by `scope_id` metadata to track per-scope progress. Claude Code's Task API does not support native scope filtering, so this convention-based approach is required.

#### Scoped Hierarchy

When decomposition occurs, the standard hierarchy uses the same P→A→C→T phases. The CODE phase gains richer behavior — sequential sub-scope execution plus cross-scope verification:

```
Feature Task (lead)
├── PREPARE Phase Task (includes scope detection)
├── ARCHITECT Phase Task (includes scope contracts + decomposition boundaries)
├── CODE Phase Task (scoped: sequential sub-scope execution + verification)
│   ├── [scope:backend-api] Sub-Feature Task (inner P→A→C→T via rePACT)
│   │   └── [scope:backend-api] Agent Tasks
│   ├── [scope:frontend-ui] Sub-Feature Task (inner P→A→C→T via rePACT)
│   │   └── [scope:frontend-ui] Agent Tasks
│   └── Scope Verification Task (contract compatibility + fulfillment)
└── TEST Phase Task (comprehensive feature testing)
```

Scope tasks are created during the CODE phase when decomposition is active. The lead follows the rePACT protocol to execute each sub-scope sequentially (inner P→A→C→T), then runs scope verification. TEST is blocked by CODE completion (which includes scope verification).

### Integration with PACT Signals

- **Algedonic signals**: Emit via task metadata or direct escalation
- **Variety signals**: Note in task metadata when complexity differs from estimate
- **Handoff**: Store structured handoff in task metadata on completion

### Example Flow

1. Lead creates Feature task: "Implement user authentication" (parent container)
2. Lead creates PREPARE phase task under the Feature task
3. Lead spawns pact-preparer teammate with agent task
4. Preparer completes, sends HANDOFF via SendMessage, updates own task to completed
5. Lead marks PREPARE complete, creates ARCHITECT phase task
6. Lead creates CODE phase task (blocked by ARCHITECT phase task)
7. Pattern continues through remaining phases

