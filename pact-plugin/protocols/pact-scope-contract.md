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
  executor: {assigned at dispatch — Agent Teams teammate}

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
- **Backend-agnostic**: The contract defines WHAT a scope delivers, not HOW. The contract format works regardless of the executor implementation.
- **Generated, not authored**: The orchestrator populates contracts from PREPARE output and detection analysis. Contracts are not hand-written.

### Generation Process

1. Identify sub-scope boundaries from detection analysis (confirmed or adjusted by user)
2. For each sub-scope:
   a. Assign `scope_id` from domain keywords (e.g., "backend-api", "frontend-ui", "database-migration")
   b. List expected deliverables from PREPARE output file references
   c. Identify interface exports/imports by analyzing cross-scope references in PREPARE output
   d. Set shared file constraints by comparing file lists across scopes — when a file appears in multiple scopes' deliverables, assign ownership to one scope (typically the scope with the most significant changes to that file); other scopes list it in `shared_files` (no-modify). The owning scope may modify the file; others must coordinate via the consolidate phase.
   e. Propagate parent conventions (from plan or ARCHITECT output if available)
3. Present contracts in the teammate spawn prompt for each sub-scope

### Contract Lifecycle

```
Detection fires → User confirms boundaries → Contracts generated
    → Passed to teammate per sub-scope → Sub-scope executes against contract
    → Sub-scope handoff (via SendMessage) includes contract fulfillment section
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

The executor interface defines the contract between the parent orchestrator (lead) and whatever mechanism fulfills a sub-scope. It is the "how" side of the scope contract: while the contract format above defines WHAT a scope delivers, the executor interface defines the input/output shape that any execution backend must implement.

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

#### Executor: Agent Teams

> **Status**: Agent Teams is experimental, gated behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.
> The mappings below reflect the current API shape but may change before official stable release.
> The executor interface abstraction insulates PACT from such changes — only the mapping table needs updating.

Agent Teams serves as the executor backend for scoped orchestration. The lead spawns one teammate per sub-scope, each operating in an isolated worktree.

| Interface Element | Agent Teams Implementation |
|-------------------|---------------------------|
| **Input: scope_contract** | Passed in the teammate spawn prompt via `Task` tool (with `team_name` and `name` parameters) |
| **Input: feature_context** | Inherited via CLAUDE.md (auto-loaded by teammates) plus the spawn prompt |
| **Input: branch** | Worktree working directory (teammate operates in the assigned worktree) |
| **Input: nesting_depth** | Communicated in the spawn prompt; no nested teams allowed (enforced by Agent Teams) |
| **Output: handoff** | `SendMessage` (type: `"message"`) from teammate to lead |
| **Output: commits** | Teammate commits directly to the feature branch in the worktree |
| **Output: status** | `TaskUpdate` via shared task list (`TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet`) |
| **Delivery mechanism** | Asynchronous — teammates operate independently; lead receives messages and task updates automatically |

**Teammate spawning example**:
```
Task(
    subagent_type="pact-backend-coder",
    team_name="{team}",
    name="scope-backend-api",
    mode="plan",
    prompt="Scope Contract: backend-api\n\nIdentity:\n  scope_id: backend-api\n  ...\n\nFeature context: ...\nBranch: feature-X--backend-api"
)
```

**PACT usage of Agent Teams tools**:

| Tool | PACT Mapping |
|------|--------------|
| `TeamCreate` | One team per scoped orchestration (or reuse session team) |
| `Task` (with `team_name`, `name`) | One teammate per sub-scope |
| `SendMessage` (type: `"message"`) | Handoff delivery, blocker reporting |
| `SendMessage` (type: `"broadcast"`) | Cross-scope coordination (used sparingly) |
| `TaskCreate`/`TaskUpdate` | Teammates self-manage their tasks |
| `TeamDelete` | Cleanup after scoped orchestration completes |

**PACT-specific notes**:

- Teammates receive only the spawn prompt (scope contract + feature context) — they do not share the lead's conversation.
- No nested teams — parallels PACT's 1-level nesting limit, enforced architecturally.
- Peer-to-peer messaging enables sibling scope coordination during the CONSOLIDATE phase.

#### Design Constraints

- **Same output shape**: The executor produces the same structured output (5-item handoff + contract fulfillment) regardless of which teammate type fulfills the scope. The consolidate phase consumes this output identically.
- **Experimental API**: The Agent Teams tool names documented above reflect the current API shape (as of early 2026). Since the feature is experimental and gated, these names may change before stable release. The executor interface abstraction insulates PACT from such changes — only the mapping table needs updating.

---

