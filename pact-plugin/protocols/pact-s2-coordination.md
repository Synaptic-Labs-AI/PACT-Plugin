## S2 Coordination Layer

The coordination layer enables parallel agent operation without conflicts. S2 is **proactive** (prevents conflicts) not just **reactive** (resolves conflicts). Apply these protocols whenever multiple agents work concurrently.

### Task System Integration

With PACT Task integration, the TaskList serves as a **shared state mechanism** for coordination:

| Use Case | How TaskList Helps |
|----------|-------------------|
| **Conflict detection** | Query TaskList to see what files/components other agents are working on |
| **Parallel agent visibility** | All in_progress agent Tasks visible via TaskList |
| **Convention propagation** | First agent's metadata (decisions, patterns) queryable by later agents |
| **Resource claims** | Agent Tasks can include metadata about claimed resources |

**Coordination via Tasks:**
```
Before parallel dispatch:
1. TaskList → check for in_progress agents on same files
2. If conflict detected → sequence or assign boundaries
3. Dispatch agents with Task IDs
4. Monitor via TaskList for completion/blockers
```

### Information Flows

S2 manages information flow between agents:

| From | To | Information |
|------|-----|-------------|
| Earlier agent | Later agents | Conventions established, interfaces defined |
| Orchestrator | All agents | Shared context, boundary assignments |
| Any agent | Orchestrator → All others | Resource claims, conflict warnings |
| TaskList | All agents | Current in_progress work, blockers, completed decisions |

### Peer Communication via SendMessage

Teammates can communicate directly with each other using `SendMessage(type: "message", recipient: "{peer-name}")`. This enables lateral coordination without routing every interaction through the lead.

**Use cases**:
- **Convention sharing**: A teammate discovers a naming pattern and notifies peers working in the same domain
- **Conflict warning**: A teammate realizes it needs to modify a file near another teammate's boundary
- **Interface coordination**: Two teammates working on connected components align on shared types or contracts

**Visibility**: The lead receives idle-notification summaries of peer messages, providing awareness of lateral communication without the overhead of being in the loop for every exchange.

**Default flow**: Peer communication is optional. The standard coordination path remains teammate-to-lead via SendMessage. Use peer DMs when direct coordination is more efficient than round-tripping through the lead.

### Pre-Parallel Coordination Check

Before invoking parallel agents, the orchestrator must:

1. **Identify potential conflicts**:
   - Shared files (merge conflict risk)
   - Shared interfaces (API contract disagreements)
   - Shared state (database schemas, config, environment)

2. **Define boundaries or sequencing**:
   - If conflicts exist, either sequence the work or assign clear file/component boundaries
   - If no conflicts, proceed with parallel invocation

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

### Shared Language

All agents operating in parallel must:
- Use project glossary and established terminology
- Use standardized handoff structure (see Phase Handoffs)

### Parallelization Anti-Patterns

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| **Sequential by default** | Missed parallelization opportunity | Run QDCL; require justification for sequential |
| **Ignoring shared files** | Merge conflicts; wasted work | QDCL catches this; sequence or assign boundaries |
| **Over-parallelization** | Coordination overhead; convention drift | Limit parallel agents; use S2 coordination |
| **Analysis paralysis** | QDCL takes longer than the work | Time-box to 1 minute; default to parallel if unclear |
| **Single agent for batch** | 4 bugs → 1 coder instead of 2-4 coders | **4+ items = multiple agents** (no exceptions) |
| **"Simpler to track" rationalization** | Sounds reasonable, wastes time | Not a valid justification; invoke concurrently anyway |
| **"Related tasks" conflation** | "Related" ≠ "dependent"; false equivalence | Related is NOT blocked; only file/data dependencies block |
| **"One agent can handle it" excuse** | Can ≠ should; missed efficiency | Capability is not justification for sequential |

**Recovery**: If in doubt, default to parallel with S2 coordination active. Conflicts are recoverable; lost time is not.

### Rationalization Detection

When you find yourself thinking these thoughts, STOP—you're rationalizing sequential dispatch:

| Thought | Reality |
|---------|---------|
| "They're small tasks" | Small = cheap to invoke together. Split. |
| "Coordination overhead" | QDCL takes 30 seconds. Split. |

**Valid reasons to sequence** (cite explicitly when choosing sequential):
- "File X is modified by both" → Sequence or define boundaries
- "A's output feeds B's input" → Sequence them
- "Shared interface undefined" → Define interface first, then parallel

### Anti-Oscillation Protocol

If agents produce contradictory outputs (each "fixing" the other's work):

1. **Detect**: Outputs conflict OR agents undo each other's work
2. **Pause**: Stop both agents immediately
3. **Diagnose**: Root cause—technical disagreement or requirements ambiguity?
4. **Resolve**:
   - Technical disagreement → Architect arbitrates
   - Requirements ambiguity → User (S5) clarifies
5. **Document**: Note resolution in handoff for future reference
6. **Resume**: Only after documented resolution

**Detection Signals**:
- Agent A modifies what Agent B just created
- Both agents claim ownership of same interface
- Output contradicts established convention
- Repeated "fix" cycles in same file/component

**Heuristic**: Consider it oscillation if the same file is modified by different agents 2+ times in rapid succession.

### Routine Information Sharing

After each specialist completes work:
1. **Extract** key decisions, conventions, interfaces established
2. **Propagate** to subsequent agents in their prompts
3. **Update** shared context for any agents still running in parallel

This transforms implicit knowledge into explicit coordination, reducing "surprise" conflicts.

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

