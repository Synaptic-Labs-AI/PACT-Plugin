## Task Hierarchy

This document explains how PACT uses Claude Code's Task system to track work at multiple levels.

### Hierarchy Levels

```
Feature Task (created by orchestrator)
├── Phase Tasks (PREPARE, ARCHITECT, CODE, TEST)
│   ├── Agent Task 1 (specialist work)
│   ├── Agent Task 2 (parallel specialist)
│   └── Agent Task 3 (parallel specialist)
└── Review Task (peer-review phase)
```

### Task Ownership

| Level | Created By | Owned By | Lifecycle |
|-------|------------|----------|-----------|
| Feature | Orchestrator | Orchestrator | Spans entire workflow |
| Phase | Orchestrator | Orchestrator | Active during phase |
| Agent | Orchestrator | Specialist (claim-only); Orchestrator (completion authority) | Specialist claims via `TaskUpdate(status="in_progress")`; orchestrator completes via `TaskUpdate(status="completed")` paired with a wake-signal SendMessage |

Under Agent Teams, specialists claim agent tasks (`pending → in_progress`) and store HANDOFFs in `metadata.handoff`, but the orchestrator transitions agent tasks to `completed` after inspecting the HANDOFF. Two narrow carve-outs (signal-tasks; secretary memory-save) self-complete; see [orchestration §Completion Authority](../skills/orchestration/SKILL.md#completion-authority).

### Task States

Tasks progress through: `pending` → `in_progress` → `completed`

- **pending**: Created but not started
- **in_progress**: Active work underway (also covers "done-awaiting-review" — teammate has stored HANDOFF and idles on `awaiting_lead_completion`)
- **completed**: Work finished (success or documented failure); transition is **lead-only** on teammate-owned tasks

### Status-by-Actor

| Transition | Actor | Conditions |
|---|---|---|
| `pending → in_progress` (claim) | Teammate | Owns the task; `blockedBy` is empty |
| `in_progress → in_progress` (metadata work) | Teammate | Writes `metadata.handoff` / `metadata.teachback_submit` |
| `in_progress → in_progress` (rejection metadata) | Lead | Writes `metadata.teachback_rejection` / `metadata.handoff_rejection` + sends wake-signal SendMessage |
| `in_progress → completed` | **LEAD ONLY** on teammate-owned tasks | Pairs with wake-signal SendMessage; carve-outs: signal-tasks, secretary memory-save, imPACT force-term |
| `pending → completed` (skip) | Lead | Phase-skip with `metadata.skipped = true` |

### Task A + Task B Dispatch Shape

Every specialist dispatch creates a Task A (teachback) + Task B (primary work) pair. Task B has `blockedBy=[A]`. Lead-completion of Task A auto-unblocks Task B in the task graph; the lead pairs the status flip with a wake-signal SendMessage so the idle teammate (on `intentional_wait{reason=awaiting_lead_completion}`) wakes to claim B. The platform does not push a wake on blocker resolution — `blockedBy` is computed at TaskList query time, so the wake-signal SendMessage is required.

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

Prefix task subjects with `[scope:{scope_id}]` to make `TaskList` output scannable:

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

The parent orchestrator iterates all tasks and filters by `scope_id` metadata to track per-scope progress. Claude Code's Task API does not support native scope filtering, so this convention-based approach is required.

#### Scoped Hierarchy

When decomposition occurs, the hierarchy extends with scope-level tasks:

```
Feature Task (root orchestrator)
├── PREPARE Phase Task (single scope, always)
├── ATOMIZE Phase Task (dispatches sub-scopes)
│   └── Scope Tasks (one per sub-scope)
│       ├── [scope:backend-api] Phase Tasks
│       │   └── [scope:backend-api] Agent Tasks
│       └── [scope:frontend-ui] Phase Tasks
│           └── [scope:frontend-ui] Agent Tasks
├── CONSOLIDATE Phase Task (cross-scope verification)
└── TEST Phase Task (comprehensive feature testing)
```

Scope tasks are created during the ATOMIZE phase. The CONSOLIDATE phase task is blocked by all scope task completions. TEST is blocked by CONSOLIDATE completion.

### Integration with PACT Signals

- **Algedonic signals**: Emit via task metadata or direct escalation
- **Variety signals**: Note in task metadata when complexity differs from estimate
- **Handoff**: Store structured handoff in task metadata on completion

### Example Flow

1. Orchestrator creates Feature task: "Implement user authentication" (parent container)
2. Orchestrator creates PREPARE phase task under the Feature task
3. Orchestrator dispatches pact-preparer with agent task (blocked by PREPARE phase task)
4. Preparer completes, updates task to completed with handoff metadata
5. Orchestrator marks PREPARE complete, creates ARCHITECT phase task
6. Orchestrator creates CODE phase task (blocked by ARCHITECT phase task)
7. Pattern continues through remaining phases

---
