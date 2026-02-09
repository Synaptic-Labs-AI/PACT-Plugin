## Scope Contract

> **Purpose**: Define what a sub-scope promises to deliver to its parent lead.
> Scope contracts are generated at decomposition time using PREPARE output and serve as
> the authoritative agreement between parent and sub-scope for deliverables and interfaces.

### Contract Format

Each sub-scope receives a scope contract with the following structure:

```
Scope Contract: {scope-name}

Identity:
  scope_id: {kebab-case identifier, e.g., "backend-api"}
  parent_scope: {parent scope_id or "root"}
  executor: {assigned at dispatch — Agent Teams (primary) or rePACT (sequential fallback)}

Deliverables:
  - {Expected file paths or patterns this scope produces}
  - {Non-file artifacts: API endpoints, schemas, migrations, etc.}

Interfaces:
  exports:
    - {Types, endpoints, APIs this scope exposes to siblings}
  imports:
    - {What this scope expects from sibling scopes}

Constraints:
  shared_files: []  # Files this scope must NOT modify (owned by siblings)
  conventions: []   # Coding conventions to follow (from parent or prior scopes)
```

### Design Principles

- **Minimal contracts** (~5-10 lines per scope): The [scope verification protocol](pact-scope-verification.md) catches what the contract does not specify. Over-specifying front-loads context cost into the lead.
- **Backend-agnostic**: The contract defines WHAT a scope delivers, not HOW. The same contract format works whether the executor is Agent Teams (primary) or rePACT (sequential fallback).
- **Generated, not authored**: The lead populates contracts from PREPARE output and detection analysis. Contracts are not hand-written.

### Generation Process

1. Identify sub-scope boundaries from detection analysis (confirmed or adjusted by user)
2. For each sub-scope:
   a. Assign `scope_id` from domain keywords (e.g., "backend-api", "frontend-ui", "database-migration")
   b. List expected deliverables from PREPARE output file references
   c. Identify interface exports/imports by analyzing cross-scope references in PREPARE output
   d. Set shared file constraints by comparing file lists across scopes — when a file appears in multiple scopes' deliverables, assign ownership to one scope (typically the scope with the most significant changes to that file); other scopes list it in `shared_files` (no-modify). The owning scope may modify the file; others must coordinate via the [scope verification protocol](pact-scope-verification.md).
   e. Propagate parent conventions (from plan or ARCHITECT output if available)
3. Present contracts in the executor dispatch prompt for each sub-scope (teammate spawn prompt for Agent Teams, or rePACT invocation prompt for sequential fallback)

### Contract Lifecycle

```
Detection fires → User confirms boundaries → Contracts generated
    → Dispatched to executor per sub-scope (Agent Teams or rePACT)
    → Sub-scope executes against contract
    → Sub-scope handoff includes contract fulfillment section
    → Scope verification protocol verifies contracts across sub-scopes
```

### Contract Fulfillment in Handoff

When a sub-scope completes, its handoff includes a contract fulfillment section mapping actual outputs to contracted items:

```
Contract Fulfillment:
  Deliverables:
    - ✅ {delivered item} → {actual file/artifact}
    - ❌ {undelivered item} → {reason}
  Interfaces:
    exports: {what was actually exposed}
    imports: {what was actually consumed from siblings}
  Deviations: {any departures from the contract, with rationale}
```

The [scope verification protocol](pact-scope-verification.md) uses fulfillment sections from all sub-scopes to verify cross-scope compatibility.

### Executor Interface

The executor interface defines the contract between the parent lead and whatever mechanism fulfills a sub-scope. It is the "how" side of the scope contract: while the contract format above defines WHAT a scope delivers, the executor interface defines the input/output shape that any execution backend must implement.

#### Interface Shape

```
Input:
  scope_contract: {the scope contract for this sub-scope}
  feature_context: {parent feature description, branch, relevant docs}
  branch: {current feature branch name}
  nesting_depth: {current nesting level, 0-based}

Output:
  handoff: {standard 5-item handoff + contract fulfillment section}
  commits: {code committed to branch}
  status: completed  # Non-happy-path uses completed with metadata (e.g., {"stalled": true} or {"blocked": true}) per task lifecycle conventions
```

#### Primary Executor: Agent Teams

Agent Teams is the primary executor for scoped orchestration. The lead spawns teammates as sub-scope executors, each receiving a scope contract and operating independently.

| Interface Element | Agent Teams Mapping |
|-------------------|---------------------|
| **Input: scope_contract** | Passed in the teammate spawn prompt via `Task` tool (with `team_name` and `name` parameters) |
| **Input: feature_context** | Inherited via CLAUDE.md (auto-loaded by teammates) plus the spawn prompt |
| **Input: branch** | Worktree working directory (teammate operates in the assigned worktree) |
| **Input: nesting_depth** | Communicated in the spawn prompt; no nested teams allowed (enforced by Agent Teams) |
| **Output: handoff** | `SendMessage` (type: `"message"`) from teammate to lead |
| **Output: commits** | Teammate commits directly to the feature branch |
| **Output: status** | `TaskUpdate` via shared task list (`TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet`) |
| **Delivery mechanism** | Asynchronous — teammates operate independently; lead receives messages and task updates automatically |

**Key Agent Teams tools**:

| Tool | Purpose | PACT Mapping |
|------|---------|--------------|
| `TeamCreate` | Create a team (with `team_name`, optional `description`) | One team per scoped orchestration |
| `Task` (with `team_name`, `name`) | Spawn a teammate into the team | One teammate per sub-scope |
| `SendMessage` (type: `"message"`) | Direct message from teammate to lead | Handoff delivery, blocker reporting |
| `SendMessage` (type: `"broadcast"`) | Message to all teammates | Cross-scope coordination (used sparingly) |
| `SendMessage` (type: `"shutdown_request"`) | Request teammate graceful exit | Sub-scope completion acknowledgment |
| `TaskCreate`/`TaskUpdate` | Shared task list management | Status tracking across sub-scopes |
| `TeamDelete` | Remove team and task directories | Cleanup after scoped orchestration completes |

**Architectural notes**:

- Teammates load CLAUDE.md, MCP servers, and skills automatically but do **not** inherit the lead's conversation history — they receive only the spawn prompt (scope contract + feature context).
- No nested teams are allowed. This parallels PACT's 1-level nesting limit but is enforced architecturally by Agent Teams rather than by convention.
- Agent Teams supports peer-to-peer messaging between teammates (`SendMessage` type: `"message"` with `recipient`), which goes beyond PACT's current hub-and-spoke model. Scoped orchestration can use this for sibling scope coordination during the [scope verification protocol](pact-scope-verification.md).

#### Sequential Fallback: rePACT

rePACT serves as the sequential execution fallback when Agent Teams is unavailable or when the lead chooses sequential sub-scope execution.

| Interface Element | rePACT Implementation |
|-------------------|-----------------------|
| **Input: scope_contract** | Passed inline in the rePACT invocation prompt by the parent lead |
| **Input: feature_context** | Inherited from parent orchestration context (branch, requirements, architecture) |
| **Input: branch** | Uses the current feature branch (no new branch created) |
| **Input: nesting_depth** | Tracked via lead context; enforced at 1-level maximum |
| **Output: handoff** | Standard 5-item handoff with Contract Fulfillment section appended (see rePACT After Completion) |
| **Output: commits** | Code committed directly to the feature branch during inner CODE phase |
| **Output: status** | Always `completed`; non-happy-path uses metadata (`{"stalled": true, "reason": "..."}` or `{"blocked": true, "blocker_task": "..."}`) per task lifecycle conventions |
| **Delivery mechanism** | Sequential — the lead executes each sub-scope's inner P→A→C→T in order, using the same Agent Teams mechanisms (SendMessage + Task metadata) for specialist dispatch within each sub-scope |

See [rePACT.md](../commands/rePACT.md) for the full command documentation, including scope contract reception and contract-aware handoff format.

#### Design Constraints

- **Backend-agnostic**: The parent lead's logic (contract generation, [scope verification protocol](pact-scope-verification.md), failure routing) does not change based on which executor fulfills the scope. Only the dispatch and collection mechanisms differ.
- **Same output shape**: Both Agent Teams and rePACT produce the same structured output (5-item handoff + contract fulfillment). The scope verification protocol consumes this output identically regardless of source.
- **Executor selection**: Agent Teams is the default executor. rePACT is used when sequential execution is preferred or when Agent Teams is unavailable. The executor interface abstraction insulates PACT from executor changes — only the mapping table needs updating.

---
