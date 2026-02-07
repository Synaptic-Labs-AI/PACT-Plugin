## Task Hierarchy

This document explains how PACT uses Claude Code's Task system to track work at multiple levels. With Agent Teams, teammates self-manage their own tasks (TaskUpdate, TaskList, TaskCreate) while the lead manages feature-level and phase-level tasks.

### Hierarchy Levels

```
Feature Task (created by lead)
├── Phase Tasks (PREPARE, ARCHITECT, CODE, TEST)
│   ├── Teammate Task 1 (specialist work, self-managed)
│   ├── Teammate Task 2 (parallel specialist, self-managed)
│   └── Teammate Task 3 (parallel specialist, self-managed)
└── Review Task (peer-review phase)
```

### Task Ownership

| Level | Created By | Managed By | Lifecycle |
|-------|------------|------------|-----------|
| Feature | Lead | Lead | Spans entire workflow |
| Phase | Lead | Lead | Active during phase |
| Teammate | Lead (pre-creates before spawning) | Teammate (claims via TaskUpdate, completes on HANDOFF) | Claimed → in_progress → completed |

Teammates have direct access to Task tools (TaskUpdate, TaskList, TaskCreate) via the pact-task-tracking skill. They claim their assigned tasks, update status, and mark completion. The lead monitors progress via TaskList and receives HANDOFFs via SendMessage.

### Task States

Tasks progress through: `pending` → `in_progress` → `completed`

- **pending**: Created but not started (lead pre-creates before spawning teammate)
- **in_progress**: Teammate has claimed the task and is actively working
- **completed**: Work finished (success or documented failure)

### Blocking Relationships

Use `addBlockedBy` to express dependencies:

```
CODE phase task
├── blockedBy: [ARCHITECT task ID]
└── Teammate tasks within CODE
    └── blockedBy: [CODE phase task ID]
```

### Metadata Conventions

Teammate tasks include metadata for context:

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

Tasks without a scope prefix belong to the root (parent) orchestrator scope.

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

When decomposition occurs, the hierarchy extends with scope-level tasks:

```
Feature Task (lead)
├── PREPARE Phase Task (single scope, always)
├── ATOMIZE Phase Task (spawns sub-scope teammates)
│   └── Scope Tasks (one per sub-scope teammate)
│       ├── [scope:backend-api] Phase Tasks (self-managed by teammate)
│       │   └── [scope:backend-api] Teammate Tasks
│       └── [scope:frontend-ui] Phase Tasks (self-managed by teammate)
│           └── [scope:frontend-ui] Teammate Tasks
├── CONSOLIDATE Phase Task (cross-scope verification)
└── TEST Phase Task (comprehensive feature testing)
```

Scope tasks are created during the ATOMIZE phase. Sub-scope teammates self-manage their internal tasks. The CONSOLIDATE phase task is blocked by all scope task completions. TEST is blocked by CONSOLIDATE completion.

### Integration with PACT Signals

- **Algedonic signals**: Teammates send via SendMessage (HALT: dual-delivery to lead + broadcast; ALERT: direct to lead). Lead creates signal Tasks and amplifies scope.
- **Variety signals**: Note in task metadata when complexity differs from estimate
- **Handoff**: Teammates send structured HANDOFF via SendMessage to lead; lead stores in task metadata on completion

### Example Flow

1. Lead creates Feature task: "Implement user authentication" (parent container)
2. Lead creates PREPARE phase task under the Feature task
3. Lead spawns pact-preparer teammate with pre-created task
4. Preparer teammate claims task (TaskUpdate → in_progress), completes work, sends HANDOFF via SendMessage, marks task completed (TaskUpdate → completed)
5. Lead marks PREPARE phase complete, shuts down preparer teammate, creates ARCHITECT phase task
6. Lead creates CODE phase task (blocked by ARCHITECT phase task)
7. Pattern continues through remaining phases — spawn teammates per phase, receive HANDOFFs via SendMessage

