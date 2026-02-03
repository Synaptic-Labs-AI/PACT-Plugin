## Scope Contract

> **Purpose**: Define what a sub-scope promises to deliver to its parent orchestrator.
> Scope contracts are generated at decomposition time using PREPARE output and serve as
> the authoritative agreement between parent and sub-scope for deliverables and interfaces.

### Contract Format

Each sub-scope receives a scope contract with the following structure:

```
Scope Contract: {scope-name}

Identity:
  scope_id: {kebab-case identifier, e.g., "backend-api"}
  parent_scope: {parent scope_id or "root"}
  executor: {assigned at dispatch — currently rePACT}

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

- **Minimal contracts** (~5-10 lines per scope): The consolidate phase catches what the contract does not specify. Over-specifying front-loads context cost into the orchestrator.
- **Backend-agnostic**: The contract defines WHAT a scope delivers, not HOW. The same contract format works whether the executor is rePACT (today) or TeammateTool (future).
- **Generated, not authored**: The orchestrator populates contracts from PREPARE output and detection analysis. Contracts are not hand-written.

### Generation Process

1. Identify sub-scope boundaries from detection analysis (confirmed or adjusted by user)
2. For each sub-scope:
   a. Assign `scope_id` from domain keywords (e.g., "backend-api", "frontend-ui", "database-migration")
   b. List expected deliverables from PREPARE output file references
   c. Identify interface exports/imports by analyzing cross-scope references in PREPARE output
   d. Set shared file constraints by comparing file lists across scopes — when a file appears in multiple scopes' deliverables, assign ownership to one scope (typically the scope with the most significant changes to that file); other scopes list it in `shared_files` (no-modify). The owning scope may modify the file; others must coordinate via the consolidate phase.
   e. Propagate parent conventions (from plan or ARCHITECT output if available)
3. Present contracts in the rePACT invocation prompt for each sub-scope

### Contract Lifecycle

```
Detection fires → User confirms boundaries → Contracts generated
    → Passed to rePACT per sub-scope → Sub-scope executes against contract
    → Sub-scope handoff includes contract fulfillment section
    → Consolidate phase verifies contracts across sub-scopes
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

The consolidate phase uses fulfillment sections from all sub-scopes to verify cross-scope compatibility.

### Executor Interface

The executor interface defines the contract between the parent orchestrator and whatever mechanism fulfills a sub-scope. It is the "how" side of the scope contract: while the contract format above defines WHAT a scope delivers, the executor interface defines the input/output shape that any execution backend must implement.

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

#### Current Executor: rePACT

rePACT implements the executor interface as follows:

| Interface Element | rePACT Implementation |
|-------------------|-----------------------|
| **Input: scope_contract** | Passed inline in the rePACT invocation prompt by the parent orchestrator |
| **Input: feature_context** | Inherited from parent orchestration context (branch, requirements, architecture) |
| **Input: branch** | Uses the current feature branch (no new branch created) |
| **Input: nesting_depth** | Tracked via orchestrator context; enforced at 2-level maximum |
| **Output: handoff** | Standard 5-item handoff with Contract Fulfillment section appended (see rePACT After Completion) |
| **Output: commits** | Code committed directly to the feature branch during Mini-Code phase |
| **Output: status** | Always `completed`; non-happy-path uses metadata (`{"stalled": true, "reason": "..."}` or `{"blocked": true, "blocker_task": "..."}`) per task lifecycle conventions |
| **Delivery mechanism** | Synchronous — agent completes and returns handoff text directly to orchestrator |

See [rePACT.md](../commands/rePACT.md) for the full command documentation, including scope contract reception and contract-aware handoff format.

#### Future Executor: TeammateTool

When Anthropic officially releases TeammateTool, it could serve as an alternative executor backend. The interface shape remains the same; only the delivery mechanism changes.

| Interface Element | Potential TeammateTool Mapping |
|-------------------|-------------------------------|
| **Input: scope_contract** | Sent via `write` operation to the spawned teammate |
| **Input: feature_context** | Passed as initial context when spawning the teammate |
| **Input: branch** | Set via teammate's working directory configuration |
| **Input: nesting_depth** | Communicated in the initial `write` message |
| **Output: handoff** | Teammate writes structured handoff to orchestrator's inbox |
| **Output: commits** | Teammate commits to the shared feature branch |
| **Output: status** | Communicated via `write` or inferred from teammate lifecycle |
| **Delivery mechanism** | Asynchronous — teammate writes to inbox files; orchestrator polls for completion |

**Environment variable alignment** (community-documented, not officially stable):

- `CLAUDE_CODE_TEAM_NAME` maps naturally to `scope_id` (team per scope)
- `CLAUDE_CODE_AGENT_TYPE` maps to the specialist domain within the scope

These mappings are noted for future reference. C5 does not depend on TeammateTool availability or API stability.

#### Design Constraints

- **Backend-agnostic**: The parent orchestrator's logic (contract generation, consolidate phase, failure routing) does not change based on which executor fulfills the scope. Only the dispatch and collection mechanisms differ.
- **Same output shape**: Both rePACT and a future TeammateTool executor produce the same structured output (5-item handoff + contract fulfillment). The consolidate phase consumes this output identically regardless of source.
- **No premature binding**: The executor interface is a protocol-level abstraction. It does not reference specific TeammateTool operation names or API signatures, which may change before official release.

---
