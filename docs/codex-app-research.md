# PACT Plugin for OpenAI Codex App — Research & Adaptation Strategy

## Table of Contents
1. [What is the Codex App?](#what-is-the-codex-app)
2. [Codex App vs Claude Code — Platform Comparison](#codex-app-vs-claude-code--platform-comparison)
3. [Extension Points Mapping](#extension-points-mapping)
4. [PACT Plugin Adaptation Strategy](#pact-plugin-adaptation-strategy)
5. [Key Differences That Affect PACT Design](#key-differences-that-affect-pact-design)
6. [How PACT Orchestration Works From Within the Codex App](#how-pact-orchestration-works-from-within-the-codex-app)
7. [Implementation Roadmap](#implementation-roadmap)

---

## What is the Codex App?

The **OpenAI Codex App** is a standalone native desktop application (macOS since Feb 2, 2026; Windows since March 4, 2026) that serves as a **command center for agentic software development**. It is distinct from:
- The Codex CLI (terminal-based)
- The Codex Cloud/Web (at chatgpt.com/codex)
- The old Codex code-completion model (deprecated)

### Key Capabilities
- **Multi-agent coordination**: Agents run in separate threads organized by projects
- **Built-in worktrees**: Multiple agents work on the same repo without conflicts via isolated Git worktrees
- **Diff review**: Review agent changes inline, comment on diffs, open in editor
- **Skills**: Extend Codex with task-specific workflows (instructions + scripts + resources)
- **Automations**: Schedule recurring agent tasks that run in the background
- **Native sandboxing**: OS-level isolation (Seatbelt on macOS, restricted tokens + ACLs on Windows)
- **Cross-platform session continuity**: Session history saved to OpenAI account

### Architecture
- Built with **Electron + Node.js**
- Uses the **Responses API** for model inference (GPT-5.4 currently)
- Shares configuration with CLI and IDE extension (`config.toml`, `AGENTS.md`, skills, MCP)
- Agent loop: prompt → tool calls → execute → feed results back → loop until done
- Auto context compaction when token usage exceeds thresholds

---

## Codex App vs Claude Code — Platform Comparison

| Dimension | Claude Code | OpenAI Codex App |
|-----------|-------------|------------------|
| **Form factor** | Terminal CLI | Native desktop app (Electron) |
| **Config file** | `CLAUDE.md` | `AGENTS.md` (open standard via Linux Foundation) |
| **Config format** | Markdown (CLAUDE.md) + JSON (settings.json) | TOML (`config.toml`) + Markdown (`AGENTS.md`) |
| **Plugin system** | `.claude-plugin/plugin.json` manifest | No formal plugin manifest; uses Skills + AGENTS.md |
| **Skills** | Skills with `SKILL.md` + references + scripts | Skills with `SKILL.md` + references + scripts (very similar!) |
| **Hooks** | Programmable Python/shell hooks at 10+ lifecycle points (SessionStart, PreToolUse, PostToolUse, SubagentStart, SubagentStop, Stop, etc.) | **No programmable hooks**; uses approval modes + sandbox config + pre-commit hooks |
| **Commands** | Slash commands (`/command-name`) | Skill invocation (`$skill-name`) |
| **Agents** | Agent Teams with shared task tools (TaskCreate, TaskGet, TaskUpdate, SendMessage) | Multi-agent via `[agents.<role>]` in config.toml (experimental) |
| **Agent communication** | Shared Task tools + SendMessage | Threads + worktrees (isolated, less inter-agent communication) |
| **MCP support** | Native MCP | Native MCP (both as client and server) |
| **Worktrees** | Manual setup via skills | Built-in first-class worktree support |
| **Automations** | Not built-in | Built-in scheduled automations |
| **Memory** | SQLite-based persistent memory + MEMORY.md + agent memory | Per-session only; no persistent cross-session memory |
| **Model** | Claude (Opus, Sonnet, Haiku) | GPT-5.4 (and variants) |
| **Sandboxing** | No built-in sandbox | Native OS-level sandbox |
| **Cost model** | API usage / subscription | Included with ChatGPT subscription |

---

## Extension Points Mapping

### How Each PACT Component Maps to Codex

| PACT Component (Claude Code) | Codex Equivalent | Adaptation Notes |
|-------------------------------|------------------|------------------|
| **`CLAUDE.md` (orchestrator prompt)** | **`AGENTS.md`** | Rewrite as AGENTS.md; 32KB default limit (configurable via `project_doc_max_bytes`). Can cascade: root AGENTS.md + subdirectory AGENTS.md files |
| **`plugin.json` (manifest)** | **No equivalent** | No plugin manifest system in Codex. Distribution via Git repos or skill catalogs instead |
| **Slash commands** (`/PACT:orchestrate`) | **Skills** (`$pact-orchestrate`) | Each command becomes a skill directory with `SKILL.md` |
| **Agent definitions** (11 .md files) | **Agent roles** in `config.toml` | Define roles via `[agents.<role>]` sections; agent instructions go in `developer_instructions` or role-specific config files |
| **Python hooks** (23 hooks) | **No direct equivalent** | This is the biggest gap. Workarounds: (1) Pre-commit hooks for git safety, (2) Skills with validation scripts, (3) `notify` config for event hooks, (4) Automations for scheduled checks |
| **Skills** (16 skills) | **Skills** | Almost 1:1 mapping! Same `SKILL.md` format, same progressive disclosure model. Place in `.codex/skills/` or `~/.codex/skills/` |
| **Protocols** (18 .md files) | **References within skills** or **AGENTS.md sections** | Bundle as skill references or include key protocols directly in AGENTS.md |
| **Memory system** (SQLite + MEMORY.md) | **No built-in equivalent** | Would need a custom MCP server for persistent memory, or use skills with scripts that manage a local DB |
| **Telegram bridge** (MCP server) | **MCP server** | Can port directly — Codex has native MCP support |
| **Agent Teams dispatch** | **Multi-agent config** | Codex multi-agent is experimental; uses role-based config rather than dynamic team creation |

### Skills — Nearly Identical Format

Both platforms use the same skill structure:

```
skill-name/
├── SKILL.md              # Required: YAML frontmatter + markdown instructions
├── scripts/              # Optional: executable code
├── references/           # Optional: additional docs loaded on demand
└── assets/               # Optional: templates, etc.
```

**Key difference**: Claude Code skills can be auto-activated by hooks; Codex skills are activated by `$skill-name` invocation or implicit matching based on the description field.

### AGENTS.md vs CLAUDE.md

| Aspect | CLAUDE.md | AGENTS.md |
|--------|-----------|-----------|
| Format | Markdown | Markdown |
| Location | Project root or `~/.claude/` | Project root or `~/.codex/`, plus subdirectories |
| Cascading | Single file | Hierarchical: root → subdirectory → CWD (closer overrides) |
| Override | No override mechanism | `AGENTS.override.md` takes precedence |
| Size limit | No hard limit | 32 KiB default (`project_doc_max_bytes`) |
| Injection | System prompt | User-role messages, root-to-leaf order |
| Standard | Anthropic-specific | Open standard (Linux Foundation, cross-tool) |
| Fallbacks | None | Configurable via `project_doc_fallback_filenames` |

---

## PACT Plugin Adaptation Strategy

### Tier 1: Core Translation (Minimum Viable PACT for Codex)

These components can be ported with relatively straightforward translation:

#### 1. AGENTS.md — The Orchestrator Prompt
Convert `CLAUDE.md` (33K words) into a cascading `AGENTS.md` structure:

```
project-root/
├── AGENTS.md                    # Core PACT orchestrator instructions (trimmed to ~32KB)
├── .codex/
│   ├── config.toml              # Agent roles, MCP servers, feature flags
│   └── skills/                  # All PACT skills
│       ├── pact-orchestrate/
│       ├── pact-compact/
│       ├── pact-plan-mode/
│       ├── pact-repact/
│       ├── pact-impact/
│       ├── pact-peer-review/
│       ├── pact-wrap-up/
│       ├── pact-pin-memory/
│       └── ... (all 16 skills)
├── agents/                      # Agent-specific AGENTS.md files
│   └── AGENTS.md                # Agent behavior guidelines
└── docs/
    ├── plans/
    ├── preparation/
    ├── architecture/
    └── review/
```

**Challenge**: The 32KB limit means CLAUDE.md must be significantly condensed. Strategy:
- Core orchestrator logic in root `AGENTS.md`
- Protocols moved into skill references (loaded on demand)
- VSM theory/glossary moved to references
- Agent dispatch details embedded in role configs

#### 2. Skills — Command Mapping
Each PACT command becomes a Codex skill:

| Claude Code Command | Codex Skill | Invocation |
|---------------------|-------------|------------|
| `/PACT:orchestrate` | `pact-orchestrate/` | `$pact-orchestrate` |
| `/PACT:comPACT` | `pact-compact/` | `$pact-compact` |
| `/PACT:plan-mode` | `pact-plan-mode/` | `$pact-plan-mode` |
| `/PACT:rePACT` | `pact-repact/` | `$pact-repact` |
| `/PACT:imPACT` | `pact-impact/` | `$pact-impact` |
| `/PACT:peer-review` | `pact-peer-review/` | `$pact-peer-review` |
| `/PACT:wrap-up` | `pact-wrap-up/` | `$pact-wrap-up` |
| `/PACT:pin-memory` | `pact-pin-memory/` | `$pact-pin-memory` |

The existing 16 PACT skills port almost directly since the `SKILL.md` format is nearly identical.

#### 3. Agent Roles — Multi-Agent Configuration
Define PACT specialist roles in `config.toml`:

```toml
[features]
multi_agent = true

[agents.preparer]
description = "Research, documentation, requirements gathering specialist"
config_file = ".codex/agents/preparer.toml"

[agents.architect]
description = "System design, component planning, interface definition specialist"
config_file = ".codex/agents/architect.toml"

[agents.backend-coder]
description = "Server-side implementation specialist"
config_file = ".codex/agents/backend-coder.toml"

[agents.frontend-coder]
description = "Client-side implementation specialist"
config_file = ".codex/agents/frontend-coder.toml"

[agents.database-engineer]
description = "Data layer implementation specialist"
config_file = ".codex/agents/database-engineer.toml"

[agents.devops-engineer]
description = "CI/CD, Docker, infrastructure specialist"
config_file = ".codex/agents/devops-engineer.toml"

[agents.test-engineer]
description = "Testing and quality assurance specialist"
config_file = ".codex/agents/test-engineer.toml"

[agents.security-engineer]
description = "Security review and hardening specialist"
config_file = ".codex/agents/security-engineer.toml"

[agents.qa-engineer]
description = "Runtime verification and exploratory testing specialist"
config_file = ".codex/agents/qa-engineer.toml"

[agents.memory-agent]
description = "Memory management and context preservation specialist"
config_file = ".codex/agents/memory-agent.toml"

[agents.n8n]
description = "n8n workflow automation specialist"
config_file = ".codex/agents/n8n.toml"

agents.max_threads = 6
agents.max_depth = 1
```

### Tier 2: Gap Bridging (Hooks & Memory)

#### 4. Hooks — The Biggest Gap

Claude Code PACT uses 23 Python hooks. Codex has **no programmable hooks**. Workarounds:

| PACT Hook | Codex Workaround |
|-----------|------------------|
| `session_init.py` (SessionStart) | Include init instructions in AGENTS.md; use a `$pact-init` skill |
| `git_commit_check.py` (PreToolUse) | Use Git pre-commit hooks (`.git/hooks/pre-commit`) |
| `team_guard.py` (PreToolUse) | Encode team validation in AGENTS.md instructions |
| `worktree_guard.py` (PreToolUse) | Built-in worktree support handles this natively |
| `validate_handoff.py` (SubagentStop) | Encode handoff validation in agent role instructions |
| `memory_enforce.py` (SubagentStop) | Include in agent role `developer_instructions` |
| `track_files.py` (PostToolUse) | Use a skill with a tracking script |
| `phase_completion.py` (Stop) | Include in `$pact-wrap-up` skill |
| `compaction_refresh.py` (SessionStart) | AGENTS.md always re-loaded; include recovery instructions |
| `notify` events | Use `notify` config key for `agent-turn-complete` events |

**Key insight**: Many hooks enforce behaviors that can instead be encoded as strong instructions in AGENTS.md and agent role configs. The trade-off is that instruction-based enforcement is softer than programmatic enforcement — the model *may* deviate, whereas hooks *guarantee* execution.

#### 5. Memory — Custom MCP Server

Codex has no built-in persistent memory. Options:

**Option A: MCP Memory Server**
Build a custom MCP server that provides memory tools:
```toml
[mcp_servers.pact-memory]
command = "python"
args = [".codex/mcp/pact-memory-server.py"]
```
This server would expose tools like `save_memory`, `search_memory`, `get_memory` backed by SQLite.

**Option B: File-Based Memory**
Use a `pact-memory/` directory with structured markdown files that agents read/write. Simpler but less powerful than the SQLite approach.

**Option C: Skill-Based Memory**
Create a `$pact-memory` skill with scripts that manage a local SQLite database, similar to the current Claude Code implementation.

### Tier 3: Enhanced Features

#### 6. Automations
Leverage Codex's built-in automation system for recurring PACT tasks:
- Scheduled code quality checks
- Periodic memory cleanup
- Automated test runs
- PR status monitoring

#### 7. Telegram Bridge
Port the existing MCP server directly — Codex has native MCP support:
```toml
[mcp_servers.telegram]
command = "python"
args = [".codex/mcp/telegram-bridge.py"]
```

---

## Key Differences That Affect PACT Design

### 1. Orchestration Model: Coordinated vs. Isolated

**Claude Code (PACT today)**: Agents share a team with TaskCreate/TaskUpdate/TaskGet/SendMessage tools. The orchestrator actively coordinates, monitors, and routes work between agents. Agents can communicate with each other.

**Codex App**: Agents run in **isolated threads with separate worktrees**. There is no built-in inter-agent communication equivalent to SendMessage/TaskGet. Each agent works independently.

**Impact on PACT**: The VSM coordination layers (S2/S3) need rethinking. In Codex, the "orchestrator" role is more about *configuring and launching* agents than *actively managing* them during execution. The PACT orchestrator may need to work more as a sequential dispatcher rather than a concurrent coordinator.

### 2. Hook Enforcement: Programmatic vs. Instructional

**Claude Code**: Hooks run as actual Python code at specific lifecycle points. They *guarantee* enforcement (e.g., `team_guard.py` prevents agent spawn without a team).

**Codex**: No lifecycle hooks. All enforcement is via instructions in AGENTS.md and role configs.

**Impact on PACT**: Some PACT guardrails (S5 policy enforcement, handoff validation, memory protocol) become "best effort" rather than "guaranteed." The AGENTS.md instructions need to be very explicit and strongly worded.

### 3. Context Window: 32KB Limit on AGENTS.md

**Claude Code**: CLAUDE.md has no hard size limit (currently 33K words / ~84KB).

**Codex**: AGENTS.md defaults to 32KB, configurable via `project_doc_max_bytes`.

**Impact on PACT**: The orchestrator prompt must be significantly condensed. Move detailed protocols, agent definitions, and reference material into skills and references that load on demand via progressive disclosure.

### 4. Agent Teams vs. Role-Based Agents

**Claude Code**: Dynamic team creation with named agents, shared task tracking, inter-agent messaging.

**Codex**: Static role definitions in config.toml with separate threads. Multi-agent is experimental.

**Impact on PACT**: Cannot dynamically create/dissolve teams per session. Agent roles must be pre-defined. The "reuse vs. spawn" decision pattern needs adaptation.

### 5. No Plugin Manifest

**Claude Code**: Has a formal `.claude-plugin/plugin.json` manifest that declares agents, commands, and skills.

**Codex**: No plugin system. Everything is configured via AGENTS.md + config.toml + skills directories.

**Impact on PACT**: Distribution model changes. Instead of "install plugin," it becomes "clone this repo's `.codex/` directory into your project" or "install these skills."

---

## How PACT Orchestration Works From Within the Codex App

This section details how to build PACT as a native Codex App experience — no external processes needed. Everything runs from within the app using its built-in multi-agent system, skills, and configuration.

### The Core Concept

In Claude Code, PACT works like this:
> User → CLAUDE.md (orchestrator prompt) → Orchestrator spawns Agent Teams → Agents use TaskCreate/SendMessage to coordinate → Hooks enforce guardrails

In the Codex App, PACT would work like this:
> User invokes `$pact-orchestrate` skill → Skill instructions turn the main agent into the PACT orchestrator → Orchestrator spawns sub-agents (specialist roles defined in config.toml) → Sub-agents work in isolated worktrees → Orchestrator collects results and coordinates phases

### Layer 1: AGENTS.md — The Orchestrator Brain

The root `AGENTS.md` defines the PACT orchestrator personality and rules. This loads automatically at session start — no hook needed.

```markdown
# PACT Orchestrator

You are 🛠️ PACT Orchestrator, the Project Manager for this codebase.
You coordinate; specialists execute. Never write application code yourself.

## Core Rules (S5 Policy)
- NEVER add, change, or remove application code directly
- ALWAYS delegate to specialist sub-agents
- Follow the PACT cycle: Prepare → Architect → Code → Test

## How to Delegate
When you receive a task:
1. Assess complexity (simple → use $pact-compact, complex → use $pact-orchestrate)
2. Spawn the right specialist sub-agents for the current phase
3. Wait for results, then advance to the next phase
4. After CODE phase, always run TEST phase before presenting results

## Sub-Agent Roles Available
Spawn these by asking Codex to "use the {role} agent":
- preparer: Research and requirements
- architect: System design
- backend-coder: Server-side implementation
- frontend-coder: Client-side implementation
- database-engineer: Data layer
- devops-engineer: CI/CD and infrastructure
- test-engineer: Testing
- security-engineer: Security review
- qa-engineer: Runtime verification
```

### Layer 2: Agent Roles in config.toml — The Specialist Definitions

Each PACT specialist becomes a Codex agent role with its own config file containing `developer_instructions`:

```toml
# .codex/config.toml (project-level)

[features]
multi_agent = true

[agents]
max_threads = 6
max_depth = 1

[agents.preparer]
description = "Research specialist. Gathers docs, maps dependencies, explores APIs. Use for PREPARE phase."
config_file = ".codex/agents/preparer.toml"
nickname_candidates = ["Scout", "Researcher"]

[agents.architect]
description = "System design specialist. Creates blueprints, defines interfaces, plans components. Use for ARCHITECT phase."
config_file = ".codex/agents/architect.toml"
nickname_candidates = ["Blueprint", "Designer"]

[agents.backend-coder]
description = "Server-side implementation specialist. Writes APIs, middleware, business logic. Use for CODE phase."
config_file = ".codex/agents/backend-coder.toml"
nickname_candidates = ["Backend", "ServerDev"]

[agents.frontend-coder]
description = "Client-side implementation specialist. Builds UI components, state management. Use for CODE phase."
config_file = ".codex/agents/frontend-coder.toml"
nickname_candidates = ["Frontend", "UIBuilder"]

[agents.test-engineer]
description = "Testing specialist. Writes and runs unit, integration, and E2E tests. Use for TEST phase."
config_file = ".codex/agents/test-engineer.toml"
nickname_candidates = ["Tester", "QA"]

[agents.security-engineer]
description = "Security review specialist. Adversarial code review, vulnerability scanning. Use for REVIEW phase."
config_file = ".codex/agents/security-engineer.toml"
nickname_candidates = ["SecOps", "Guardian"]
```

Each agent's `.toml` file contains role-specific instructions:

```toml
# .codex/agents/backend-coder.toml
model = "gpt-5.4"
model_reasoning_effort = "high"
developer_instructions = """
You are pact-backend-coder, a server-side implementation specialist.

## Your Role
- Implement server-side code: APIs, middleware, business logic, data processing
- Write clean, maintainable, secure code following project conventions

## Before Starting
1. Read the AGENTS.md in the current directory for project conventions
2. Check docs/architecture/ for design decisions from the architect
3. Check docs/preparation/ for research context from the preparer

## When Done
Create a structured HANDOFF summary:
1. Files created/modified
2. Key decisions with rationale
3. Areas of uncertainty (HIGH/MEDIUM/LOW)
4. Integration points affected
5. Open questions

Write this to docs/handoffs/backend-coder-{timestamp}.md
"""
```

### Layer 3: Skills — The PACT Commands

Each PACT command becomes a skill that the orchestrator (or user) invokes:

#### `$pact-orchestrate` — Full Multi-Agent Workflow

```
.codex/skills/pact-orchestrate/
├── SKILL.md
├── scripts/
│   └── assess_complexity.py    # Variety scoring script
└── references/
    ├── phase-transitions.md    # When/how to move between phases
    └── delegation-rules.md     # What to delegate to whom
```

```yaml
# .codex/skills/pact-orchestrate/SKILL.md
---
name: pact-orchestrate
description: Full PACT multi-agent workflow. Use for complex tasks requiring multiple specialists across Prepare, Architect, Code, and Test phases.
---

## When to Use
Use when a task requires:
- Multiple phases (research → design → implementation → testing)
- Multiple specialist domains (backend + frontend, or any combination)
- Architectural decisions before coding

## Workflow

### Step 1: Assess Complexity
Run `scripts/assess_complexity.py` to score the task on novelty, scope, uncertainty, and risk.

### Step 2: PREPARE Phase
Spawn a `preparer` sub-agent with the task context.
Wait for their handoff in `docs/handoffs/`.
The preparer will research documentation, map dependencies, and gather requirements.

### Step 3: ARCHITECT Phase
Spawn an `architect` sub-agent, pointing them to the preparer's handoff.
Wait for their design documents in `docs/architecture/`.

### Step 4: CODE Phase
Based on the architect's design, spawn the appropriate coder sub-agents IN PARALLEL:
- Backend changes → spawn `backend-coder`
- Frontend changes → spawn `frontend-coder`
- Database changes → spawn `database-engineer`
- Multiple domains → spawn multiple coders simultaneously

Each coder works in their own worktree. Wait for all to complete.

### Step 5: TEST Phase
Spawn a `test-engineer` sub-agent pointing to all coder handoffs.
The test engineer writes and runs tests for everything produced in Step 4.

### Step 6: Review
Spawn `security-engineer` and the relevant domain coder in parallel for peer review.
Collect findings in `docs/review/`.

### Step 7: Present Results
Summarize all phase outcomes to the user. Offer to create a PR.
```

#### `$pact-compact` — Single Specialist, Light Process

```yaml
# .codex/skills/pact-compact/SKILL.md
---
name: pact-compact
description: Light PACT workflow using a single specialist agent. Use for focused tasks that need one domain expert.
---

## When to Use
- Bug fixes in a single domain
- Small features touching one area
- Quick refactors

## Workflow
1. Identify the right specialist role for the task
2. Spawn ONE sub-agent of that role
3. Wait for their handoff
4. Present results to user
```

#### `$pact-plan-mode` — Strategic Planning

```yaml
# .codex/skills/pact-plan-mode/SKILL.md
---
name: pact-plan-mode
description: Strategic planning consultation. Spawns preparer and architect to create an implementation plan WITHOUT writing any code.
---

## Workflow
1. Spawn `preparer` to research the task
2. Spawn `architect` to design the approach based on research
3. Combine into a plan document at `docs/plans/`
4. Present plan to user for approval
5. Do NOT proceed to CODE phase — this is planning only
```

### Layer 4: Handoff Communication via Files

Since Codex sub-agents don't have Claude Code's `SendMessage`/`TaskGet`, PACT uses **file-based handoffs** — each agent writes structured results to `docs/handoffs/`:

```
docs/
├── handoffs/
│   ├── preparer-2026-03-09T10-30.md      # Preparer's research output
│   ├── architect-2026-03-09T11-00.md      # Architect's design output
│   ├── backend-coder-2026-03-09T12-00.md  # Backend coder's implementation notes
│   └── test-engineer-2026-03-09T13-00.md  # Test engineer's results
├── preparation/    # Detailed research artifacts
├── architecture/   # Design documents
├── plans/          # Implementation plans
├── decision-logs/  # Why decisions were made
└── review/         # Review findings
```

The orchestrator reads these files to understand what each agent produced and feed context to the next phase. This replaces Claude Code's `TaskGet(taskId).metadata.handoff` pattern.

### Layer 5: Replacing Hooks with Skills + Instructions

| Claude Code Hook | Codex App Replacement |
|---|---|
| `session_init.py` (SessionStart) | AGENTS.md auto-loads at session start; `$pact-init` skill for explicit setup |
| `git_commit_check.py` (PreToolUse) | Git pre-commit hooks in `.git/hooks/` or `.husky/` |
| `team_guard.py` (PreToolUse) | Strong instruction in AGENTS.md: "Before spawning agents, verify multi_agent is enabled" |
| `validate_handoff.py` (SubagentStop) | Instruction in each agent's `developer_instructions`: "You MUST write a structured handoff to docs/handoffs/" |
| `memory_enforce.py` (SubagentStop) | Instruction in `developer_instructions` + `$pact-memory` skill |
| `track_files.py` (PostToolUse) | Instruction: "Maintain a file change log in docs/handoffs/" |
| `compaction_refresh.py` | AGENTS.md re-reads automatically; handoff files persist on disk |
| `phase_completion.py` (Stop) | `$pact-wrap-up` skill for explicit session cleanup |

### Layer 6: The CSV Fan-Out Pattern for Parallel Work

For tasks with many similar sub-items (e.g., "fix these 5 bugs"), PACT can use Codex's `spawn_agents_on_csv` pattern:

```
1. Orchestrator creates /tmp/pact-tasks.csv:
   task_id,domain,description,phase
   1,backend,"Fix auth token expiry",CODE
   2,backend,"Add rate limiting to /api/users",CODE
   3,frontend,"Fix dark mode toggle",CODE

2. Orchestrator calls spawn_agents_on_csv with instruction:
   "You are a {domain}-coder specialist. Implement: {description}.
    Write handoff to docs/handoffs/{task_id}.md.
    Call report_agent_job_result with your summary."

3. Codex spawns 3 parallel agents, each in its own worktree
4. Agents report results → orchestrator gets consolidated CSV
```

This maps directly to how Claude Code PACT dispatches "3 backend-coders in parallel for 3 independent bugs."

### Layer 7: Automations for Recurring PACT Tasks

Use Codex's built-in automations for tasks that PACT currently handles ad-hoc:

```
Automation: "pact-quality-check"
Schedule: Every commit to main
Skill: $pact-peer-review
Action: Run security + architecture review on latest changes
Output: Results in inbox (archive if clean)

Automation: "pact-memory-cleanup"
Schedule: Weekly
Skill: $pact-memory
Action: Prune stale handoffs, consolidate decision logs
Output: Summary in inbox
```

### Putting It All Together — User Experience

**From the user's perspective in the Codex App:**

1. Open a project in the Codex App
2. Type: `$pact-orchestrate Add user authentication with OAuth2`
3. The skill loads → main agent becomes the PACT orchestrator
4. Orchestrator spawns a `preparer` agent in a worktree → researches OAuth2 patterns
5. Preparer writes handoff → orchestrator reads it
6. Orchestrator spawns `architect` agent → designs the auth system
7. Architect writes handoff → orchestrator reads it
8. Orchestrator spawns `backend-coder` and `frontend-coder` in parallel (separate worktrees)
9. Both coders write handoffs → orchestrator reads them
10. Orchestrator spawns `test-engineer` → writes and runs tests
11. Orchestrator spawns `security-engineer` → reviews auth implementation
12. All results collected → orchestrator presents summary with diff review
13. User reviews diffs, comments, stages/reverts, and commits

The user sees multiple agent threads in the app sidebar, each working in isolation, with the orchestrator coordinating phases sequentially.

### What You Gain vs. Lose Compared to Claude Code PACT

**Gains:**
- Native worktree isolation (no manual setup needed)
- Visual multi-thread UI (see all agents at once)
- Built-in diff review and inline commenting
- Automations for scheduled PACT workflows
- Cheaper per-token cost
- Cross-platform desktop experience

**Losses:**
- No programmatic hooks (enforcement is instruction-based, not guaranteed)
- No inter-agent messaging (file-based handoffs instead of SendMessage)
- No persistent cross-session memory (needs custom MCP server)
- 32KB AGENTS.md limit (must condense orchestrator prompt)
- Multi-agent is experimental (may change)
- No formal plugin manifest or marketplace

---



### Phase 1: Foundation
1. Create condensed `AGENTS.md` from `CLAUDE.md` (fit within 32KB)
2. Port the 9 PACT commands as Codex skills (`$pact-orchestrate`, etc.)
3. Define 11 agent roles in `config.toml`
4. Port existing 16 skills (minimal changes needed — format is nearly identical)

### Phase 2: Protocols & Safety
5. Encode key protocols as skill references
6. Implement hook equivalents via AGENTS.md instructions and pre-commit hooks
7. Create `$pact-init` skill for session initialization
8. Add handoff validation instructions to all agent role configs

### Phase 3: Memory & Communication
9. Build MCP memory server (or file-based alternative)
10. Port Telegram MCP bridge
11. Create inter-agent communication patterns using Codex's thread model

### Phase 4: Automation & Polish
12. Set up PACT automations (scheduled quality checks, memory cleanup)
13. Create installation/setup documentation
14. Build a `$pact-setup` skill for one-command project initialization
15. Test and iterate on real projects

---

## Distribution Model

### For Claude Code (Current)
```
Plugin marketplace → /plugin install → copies to ~/.claude/plugins/
```

### For Codex App (Proposed)
```
Git repo → clone .codex/ directory → or install skills individually
```

Possible distribution approaches:
1. **Git template repo**: Users clone/copy `.codex/` and `AGENTS.md` into their project
2. **Skill catalog**: Publish individual PACT skills to the OpenAI skills catalog (github.com/openai/skills)
3. **Setup skill**: Create a `$pact-setup` skill that bootstraps the full PACT configuration
4. **NPM/pip package**: CLI tool that scaffolds PACT config into a project

---

## Codex App Server — A Key Integration Point

The Codex desktop app is powered by the **Codex App Server**, an open-source bidirectional JSON-RPC API (`openai/codex/codex-rs/app-server/`). This is a significant integration point that could enable deeper PACT orchestration.

### Protocol Details
- **Transport**: JSON-RPC 2.0 over stdio (JSONL) or experimental WebSocket
- **Client bindings**: Go, Python, TypeScript, Swift, Kotlin
- **Start**: `codex app-server` (stdio) or `codex app-server --listen ws://127.0.0.1:4500` (WebSocket)

### Key Methods
| Method | Purpose |
|--------|---------|
| `review/start` | Initiate code review |
| `command/exec` | Execute commands |
| `model/list` | List available models |
| `skills/list` | List installed skills |
| `skills/config/write` | Configure skills |
| `app/list` | List app instances |

### Streaming & Approvals
- Items stream with lifecycle events: `item/started`, `item/*/delta`, `item/completed`
- Server-initiated approval requests for command execution and file changes
- Network approvals grouped by destination (host, protocol, port)

### What This Means for PACT
The App Server protocol opens the door to building a **PACT orchestrator as an external process** that controls multiple Codex agents programmatically — potentially recovering some of the coordination capabilities lost from Claude Code's hook system. A PACT orchestrator could:

1. Spawn and manage multiple agent threads via the JSON-RPC API
2. Monitor agent progress via streaming events
3. Implement approval policies programmatically (replacing hooks)
4. Coordinate inter-agent communication externally
5. Manage memory persistence across sessions

This is a more advanced integration path but would yield the closest equivalent to PACT's current Claude Code architecture.

---

## Sources

- [Introducing the Codex App — OpenAI](https://openai.com/index/introducing-the-codex-app/)
- [Codex App Documentation](https://developers.openai.com/codex/app)
- [OpenAI Codex on Windows — Dataconomy](https://dataconomy.com/2026/03/05/openai-launches-standalone-codex-coding-app-for-windows/)
- [Codex App on Windows — Engadget](https://www.engadget.com/ai/openai-brings-its-codex-coding-app-to-windows-195345429.html)
- [Codex Skills Documentation](https://developers.openai.com/codex/skills/)
- [Codex Skills GitHub](https://github.com/openai/skills)
- [AGENTS.md Guide](https://developers.openai.com/codex/guides/agents-md/)
- [AGENTS.md Open Standard](https://agents.md/)
- [Codex Multi-Agent Documentation](https://developers.openai.com/codex/multi-agent/)
- [Codex Configuration Reference](https://developers.openai.com/codex/config-reference)
- [Codex Advanced Configuration](https://developers.openai.com/codex/config-advanced/)
- [Unrolling the Codex Agent Loop — OpenAI](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [Codex App Automations](https://developers.openai.com/codex/app/automations/)
- [Codex vs Claude Code — Builder.io](https://www.builder.io/blog/codex-vs-claude-code)
- [Skills in OpenAI Codex — Jesse Vincent](https://blog.fsck.com/2025/12/19/codex-skills/)
- [Codex Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide/)
- [Codex App Server Documentation](https://developers.openai.com/codex/app-server/)
- [Unlocking the Codex Harness: How We Built the App Server — OpenAI](https://openai.com/index/unlocking-the-codex-harness/)
- [Codex App Server Architecture — InfoQ](https://www.infoq.com/news/2026/02/opanai-codex-app-server/)
- [Codex App Features](https://developers.openai.com/codex/app/features/)
- [Codex Worktrees Documentation](https://developers.openai.com/codex/app/worktrees/)
- [Codex Local Environments](https://developers.openai.com/codex/app/local-environments/)
- [Use Codex with the Agents SDK](https://developers.openai.com/codex/guides/agents-sdk/)
- [Codex GitHub Repository](https://github.com/openai/codex)
