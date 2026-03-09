# PACT Plugin for OpenAI Codex App — Research & Adaptation Strategy

## Table of Contents
1. [What is the Codex App?](#what-is-the-codex-app)
2. [Codex App vs Claude Code — Platform Comparison](#codex-app-vs-claude-code--platform-comparison)
3. [Extension Points Mapping](#extension-points-mapping)
4. [PACT Plugin Adaptation Strategy](#pact-plugin-adaptation-strategy)
5. [Key Differences That Affect PACT Design](#key-differences-that-affect-pact-design)
6. [Implementation Roadmap](#implementation-roadmap)

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

## Implementation Roadmap

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
