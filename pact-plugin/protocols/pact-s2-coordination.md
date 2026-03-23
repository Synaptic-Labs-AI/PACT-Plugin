## S2 Coordination Layer

The coordination layer enables parallel agent operation without conflicts. S2 is **proactive** (prevents conflicts) not just **reactive** (resolves conflicts). Apply these protocols whenever multiple agents work concurrently.

### Task System Integration

With PACT Task integration, the `TaskList` serves as a **shared state mechanism** for coordination:

| Use Case | How `TaskList` Helps |
|----------|-------------------|
| **Conflict detection** | Query `TaskList` to see what files/components other agents are working on |
| **Parallel agent visibility** | All in_progress agent Tasks visible via `TaskList` |
| **Convention propagation** | First agent's metadata (decisions, patterns) queryable by later agents |
| **Resource claims** | Agent Tasks can include metadata about claimed resources |

**Coordination via Tasks:**
```
Before parallel dispatch:
1. `TaskList` → check for in_progress agents on same files
2. If conflict detected → sequence or assign boundaries
3. Dispatch agents with Task IDs
4. Monitor via `TaskList` for completion/blockers
```

### Information Flows

S2 manages information flow between agents:

| From | To | Information |
|------|-----|-------------|
| Earlier agent | Later agents | Conventions established, interfaces defined |
| Orchestrator | All agents | Shared context, boundary assignments |
| Any agent | Orchestrator → All others | Resource claims, conflict warnings |
| `TaskList` | All agents | Current in_progress work, blockers, completed decisions |

### Pre-Parallel Coordination Check

Before invoking parallel agents, the orchestrator must:

1. **Identify potential conflicts**:
   - Shared files (merge conflict risk)
   - Shared interfaces (API contract disagreements)
   - Shared state (database schemas, config, environment)

2. **Define boundaries or sequencing**:
   - If conflicts exist, either sequence the work or assign clear file/component boundaries
   - If no conflicts, proceed with parallel invocation
   - **Persist `s2_boundaries`**: `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {"agent_name": ["file_paths"]}})`

3. **Establish resolution authority**:
   - Technical disagreements → Architect arbitrates
   - Style/convention disagreements → First agent's choice becomes standard
   - Resource contention → Orchestrator allocates

### S2 Pre-Parallel Checkpoint Format

When analyzing parallel work, emit proactive coordination signals:

> **S2 Pre-Parallel Check**:
> - Shared files: [none / list with mitigation]
> - Shared interfaces: [none / contract defined by X]
> - Conventions: [pre-defined / first agent establishes]
> - Anticipated conflicts: [none / sequencing X before Y]

**Example**:
> **S2 Pre-Parallel Check**:
> - Shared files: `src/types/api.ts` — Backend defines, Frontend consumes (read-only)
> - Shared interfaces: API contract defined in architecture doc
> - Conventions: Follow existing patterns in `src/utils/`
> - Anticipated conflicts: None

### Conflict Resolution

| Conflict Type | Resolution |
|---------------|------------|
| Same file | Sequence agents OR assign clear section boundaries |
| Interface disagreement | Architect arbitrates; document decision |
| Naming/convention | First agent's choice becomes standard for the batch |
| Resource contention | Orchestrator allocates; others wait or work on different tasks |

### Convention Propagation

When "first agent's choice becomes standard," subsequent agents need to discover those conventions:

1. **Orchestrator responsibility**: When invoking parallel agents after the first completes:
   - Extract key conventions from first agent's output (naming patterns, file structure, API style)
   - Include in subsequent agents' prompts: "Follow conventions established: {list}"

2. **Handoff reference**: Orchestrator passes first agent's key decisions to subsequent agents

3. **For truly parallel invocation** (all start simultaneously):
   - Orchestrator pre-defines conventions in all prompts
   - Or: Run one agent first to establish conventions, then invoke the rest concurrently
   - **Tie-breaker**: If agents complete simultaneously and no first-agent convention exists, use alphabetical domain order (backend, database, frontend) for convention precedence

4. **Persist `established_conventions`**: `TaskUpdate(codePhaseTaskId, metadata={"established_conventions": {"naming": "...", "patterns": "...", "style": "..."}})`

> **State recovery**: After compaction, read `TaskGet(codePhaseTaskId).metadata` to recover `s2_boundaries` and `established_conventions` before dispatching subsequent agents.

### Shared Language

All agents operating in parallel must:
- Use project glossary and established terminology
- Use standardized handoff structure (see [Phase Handoffs](pact-phase-transitions.md#phase-handoffs))

### Parallelization Rules

**Default**: Parallel. Sequence ONLY for file/data dependencies. If in doubt, parallel with S2 coordination active. Conflicts are recoverable; lost time is not.

**Anti-patterns**: Sequential by default, ignoring shared files, "simpler to track" rationalization, "related tasks" conflation (related ≠ dependent), single agent for batch (4+ items = multiple agents).

**Valid reasons to sequence** (cite explicitly):
- "File X is modified by both" → Sequence or define boundaries
- "A's output feeds B's input" → Sequence them
- "Shared interface undefined" → Define interface first, then parallel

### Routine Information Sharing

After each specialist completes work:
1. **Extract** key decisions, conventions, interfaces established
2. **Propagate** to subsequent agents in their prompts
3. **Update** shared context for any agents still running in parallel

This transforms implicit knowledge into explicit coordination, reducing "surprise" conflicts.

### Environment Drift Detection

When dispatching agents during parallel execution, the codebase may have changed since earlier agents were briefed. Use the file tracking system to detect environment drift.

**Before dispatching a new agent (when other agents have already modified files)**:
1. Check the team's `file-edits.json` (maintained by `file_tracker.py`) for files modified since the session started or since the last dispatch
2. If files relevant to the new agent's scope were modified, include an environment delta in the dispatch prompt:
   > "Since your context was set, these files were modified: `src/auth.ts` (by backend-coder), `src/types.ts` (by database-engineer). Review before making assumptions about their current state."
3. This is not a full re-briefing — just a delta awareness signal

**When an agent completes and another agent is still running**:
- Check if the completing agent modified files in the running agent's scope
- If so, send a brief `SendMessage` to the running agent: "Environment changed: {file} was modified by {agent}. Verify your assumptions about it."

**Skip when**: Single-agent execution (no parallel agents = no drift risk).

### Semantic Overlap Detection

Beyond file-level conflicts (Layer 1, handled by Pre-Parallel Coordination Check above), agents can semantically overlap — implementing the same concept independently even when touching different files.

**Three-layer detection**:

| Layer | What It Checks | Precision | Cost |
|-------|---------------|-----------|------|
| 1. File scope intersection | Assigned file paths overlap | High | Cheap (set intersection) |
| 2. Interface contract overlap | Shared dependencies, data types, or API endpoints | High | Cheap (metadata comparison) |
| 3. Semantic field matching | Task description keyword extraction + concept clustering | Medium | Medium (keyword extraction) |

**Layer 2 — Interface Contract Overlap**: Compare structured metadata fields across agent tasks. If agents share dependencies, data types, or API endpoints, flag as potential overlap. The orchestrator populates these fields during CODE phase dispatch as part of the "define boundaries" step.

**Layer 3 — Semantic Field Matching**: Extract significant terms from task descriptions (filtering common stop words like "error", "validation", "config", "handler", "service", "model"). Cluster by prefix (e.g., "auth-flow", "auth-token" → "auth"). If agents share 2+ concepts, flag for review.

**Severity matrix**:

| Layers Triggered | Severity | Recommended Action |
|-----------------|----------|-------------------|
| Layer 1 only | **High** | Sequence or assign strict file boundaries |
| Layer 2 only | **Medium** | Define contract authority; document who owns the shared interface |
| Layer 3 only | **Low** | Note for review; may self-resolve; pass to auditor as focus area |
| Layer 1 + 2 | **High** | Must resolve before parallel dispatch |
| Layer 2 + 3 | **Medium** | Define contracts + assign concept ownership |
| All three | **Critical** | Consider sequencing instead of parallel |

**Integration**: Run semantic overlap detection during S2 Pre-Parallel Check. Layer 1 is already implemented above. Layers 2 and 3 extend the check with richer metadata analysis. If the concurrent auditor is active, pass Layer 3 (Low severity) findings as focus areas.

---
## Backend ↔ Database Boundary

**Sequence**: Database delivers schema → Backend implements ORM.

| Database Engineer Owns | Backend Engineer Owns |
|------------------------|----------------------|
| Schema design, DDL | ORM models |
| Migrations | Repository/DAL layer |
| Complex SQL queries | Application queries via ORM |
| Indexes | Connection pooling |

**Collaboration**: If Backend needs a complex query, ask Database. If Database needs to know access patterns, ask Backend.

---
