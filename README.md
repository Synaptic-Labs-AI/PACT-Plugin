# PACT Framework for Claude Code

> **Principled AI-assisted Coding through Teamwork** — Transform Claude Code into a coordinated team of specialist developers using the Prepare → Architect → Code → Test methodology.

## What is PACT?

PACT turns a single AI assistant into **11 specialist agents** that work together systematically:

| Agent | Role |
|-------|------|
| **Preparer** | Research, gather requirements, read docs |
| **Architect** | Design systems, create blueprints |
| **Backend Coder** | Implement server-side logic |
| **Frontend Coder** | Build user interfaces |
| **Database Engineer** | Design schemas, optimize queries |
| **DevOps Engineer** | CI/CD, Docker, infrastructure, build systems |
| **n8n Specialist** | Build workflow automations |
| **Test Engineer** | Write comprehensive tests |
| **Security Engineer** | Adversarial security code review |
| **QA Engineer** | Runtime verification, exploratory testing |
| **Memory Agent** | Persist context, recover from compaction |

Instead of "vibe coding" (letting AI guess), PACT ensures **preparation before coding**, **architecture before implementation**, and **testing as integral**.

---

## Installation

### Option A: Let Claude Set It Up (Easiest)

Just give Claude this prompt:

```
Read the PACT setup instructions at https://github.com/ProfSynapse/PACT-prompt/blob/main/README.md
and help me install the PACT plugin with auto-updates enabled.
```

Or copy this detailed prompt for Claude:

```
Help me install the PACT Framework plugin for Claude Code:

1. Add the marketplace: /plugin marketplace add ProfSynapse/PACT-prompt
2. Install the plugin: /plugin install PACT@pact-marketplace
3. Enable auto-updates via /plugin → Marketplaces → pact-marketplace → Enable auto-update
4. Set up the orchestrator by appending PACT's CLAUDE.md to my existing ~/.claude/CLAUDE.md
   (or create it if I don't have one)
5. Tell me to restart Claude Code
```

### Option B: Manual Installation

**Step 1: Add the marketplace**
```bash
/plugin marketplace add ProfSynapse/PACT-prompt
```

**Step 2: Install the plugin**
```bash
/plugin install PACT@pact-marketplace
```

**Step 3: Enable auto-updates**
- Run `/plugin`
- Select **Marketplaces**
- Select **pact-marketplace**
- Enable **Auto-update**

**Step 4: Set up the Orchestrator**

The PACT Orchestrator needs to be in your global `CLAUDE.md`:

```bash
# If you DON'T have an existing ~/.claude/CLAUDE.md:
cp ~/.claude/plugins/cache/pact-marketplace/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md

# If you DO have an existing ~/.claude/CLAUDE.md, append PACT to it:
cat ~/.claude/plugins/cache/pact-marketplace/PACT/*/CLAUDE.md >> ~/.claude/CLAUDE.md
```

**Step 5: Restart Claude Code**
```bash
exit
claude
```

### Option C: Clone for Development

If you want to contribute or customize PACT:

```bash
git clone https://github.com/ProfSynapse/PACT-prompt.git
cd PACT-prompt
claude
```

### ⚠️ Restart Required

After installing, you **must restart Claude Code**:

1. Type `exit` or close the terminal
2. Run `claude` again

This loads all agents, hooks, and skills properly.

### Verifying Installation

After restart, test with:
```
/PACT:orchestrate Hello, confirm PACT is working
```

You should see the 🛠️ PACT Orchestrator respond.

---

## Quick Start

Once installed, start Claude Code in your project:

```bash
claude
```

Then use natural language or commands:

```
# Natural language
"I want to build a REST API for user management. Start the PACT process."

# Or use commands directly
/PACT:orchestrate Build user authentication with JWT
/PACT:comPACT backend Fix the null pointer in auth middleware
/PACT:plan-mode Design a caching strategy for our API
```

---

## Commands

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `/PACT:orchestrate` | Full multi-agent workflow | New features, complex tasks |
| `/PACT:comPACT` | Single specialist, light process | Quick fixes, focused tasks |
| `/PACT:plan-mode` | Planning consultation (no code) | Before complex implementations |
| `/PACT:rePACT` | Nested PACT cycle for sub-tasks | Complex sub-problems during CODE |
| `/PACT:imPACT` | Triage when blocked | Hit a blocker, need help deciding |
| `/PACT:peer-review` | Commit, PR, multi-agent review | Ready to merge |
| `/PACT:pin-memory` | Pin critical context permanently | Gotchas, key decisions to preserve |
| `/PACT:wrap-up` | End-of-session cleanup | Ending a work session |

### comPACT Examples

```bash
/PACT:comPACT backend Fix the authentication bug
/PACT:comPACT frontend Add loading spinner to submit button
/PACT:comPACT database Add index to users.email column
/PACT:comPACT test Add unit tests for payment module
/PACT:comPACT architect Should we use microservices here?
/PACT:comPACT prepare Research OAuth2 best practices
```

---

## Skills (13 Domain Knowledge Modules)

Skills provide specialized knowledge that loads on-demand:

### PACT Phase Skills
| Skill | Triggers On |
|-------|-------------|
| `pact-prepare-research` | Research, requirements, API exploration |
| `pact-architecture-patterns` | System design, C4 diagrams, patterns |
| `pact-coding-standards` | Clean code, error handling, conventions |
| `pact-testing-strategies` | Test pyramid, coverage, mocking |
| `pact-security-patterns` | Auth, OWASP, credential handling |

### n8n Workflow Skills
| Skill | Triggers On |
|-------|-------------|
| `n8n-workflow-patterns` | Workflow architecture, webhooks |
| `n8n-node-configuration` | Node setup, field dependencies |
| `n8n-expression-syntax` | Expressions, `$json`, `$node` |
| `n8n-code-javascript` | JavaScript in Code nodes |
| `n8n-code-python` | Python in Code nodes |
| `n8n-validation-expert` | Validation errors, debugging |
| `n8n-mcp-tools-expert` | MCP tool usage |

### Context Management Skills
| Skill | Triggers On |
|-------|-------------|
| `pact-memory` | Save/search memories, lessons learned |

---

## Memory System

PACT includes a persistent memory system for cross-session learning:

```python
# Save context, decisions, lessons learned
memory.save({
    "context": "Building authentication system",
    "goal": "Add JWT refresh tokens",
    "lessons_learned": ["Always hash passwords with bcrypt"],
    "decisions": [{"decision": "Use Redis", "rationale": "Fast TTL"}],
    "entities": [{"name": "AuthService", "type": "component"}]
})

# Semantic search across all memories
memory.search("rate limiting")
```

**Features:**
- Local SQLite database with vector embeddings
- Graph network linking memories to files
- Semantic search across sessions
- Auto-prompts to save after significant work

**Storage:** `~/.claude/pact-memory/` (persists across projects)

---

## Project Structure

### Plugin Installation (Recommended)

When installed as a plugin, PACT lives in your plugin cache:

```
~/.claude/
├── CLAUDE.md                   # Orchestrator (copy from plugin)
├── plugins/
│   └── cache/
│       └── pact-marketplace/
│           └── PACT/
│               └── 3.2.0/      # Plugin version
│                   ├── agents/
│                   ├── commands/
│                   ├── skills/
│                   ├── hooks/
│                   └── protocols/
├── protocols/
│   └── pact-plugin/            # Symlink to plugin protocols
└── pact-memory/                # Memory database (shared)
    ├── memory.db
    └── models/
        └── all-MiniLM-L6-v2.gguf
```

### Your Project

```
your-project/
├── CLAUDE.md                   # Project-specific config (optional)
└── docs/
    ├── plans/                  # Implementation plans
    ├── architecture/           # Design documents
    ├── decision-logs/          # Implementation decisions
    └── preparation/            # Research outputs
```

### Development Clone

If you cloned this repo for development/contribution:

```
PACT-prompt/
├── .claude-plugin/
│   └── marketplace.json        # Self-hosted marketplace definition
├── pact-plugin/                # Plugin source (canonical)
│   ├── .claude-plugin/
│   │   └── plugin.json         # Plugin definition
│   ├── agents/                 # 11 specialist agents
│   ├── commands/               # 8 PACT workflow commands
│   ├── skills/                 # 13 domain knowledge skills
│   ├── hooks/                  # Automation hooks
│   ├── protocols/              # Coordination protocols
│   └── CLAUDE.md               # Orchestrator configuration
└── docs/
```

---

## How It Works

### The PACT Cycle

```
┌─────────────────────────────────────────────────────────────┐
│                    /PACT:orchestrate                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   PREPARE ──► ARCHITECT ──► CODE ──► TEST                   │
│      │            │           │         │                   │
│      ▼            ▼           ▼         ▼                   │
│   Research    Design      Implement   Verify                │
│   Docs        Blueprint   Backend     Unit tests            │
│   APIs        Contracts   Frontend    Integration           │
│   Context     Schema      Database    E2E tests             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### VSM-Enhanced Orchestration

PACT uses the **Viable System Model** for intelligent coordination:

- **Variety Management**: Simple tasks get light process; complex tasks get full ceremony
- **Adaptive Workflow**: Orchestrator selects the right level of rigor
- **Viability Sensing**: Agents emit emergency signals (HALT/ALERT) for critical issues
- **Continuous Audit**: Quality feedback during implementation, not just at the end

### Hooks (Automation)

| Hook | Trigger | Purpose |
|------|---------|---------|
| `session_init.py` | Session start | Load active plans, check memory |
| `phase_completion.py` | Agent completes | Remind about decision logs |
| `validate_handoff.py` | Agent handoff | Verify output quality |
| `track_files.py` | File edit/write | Track files for memory graph |
| `memory_prompt.py` | Session end | Prompt to save learnings |

---

## Configuration

### CLAUDE.md

The `CLAUDE.md` file configures the orchestrator. Key sections:

```markdown
# MISSION
Act as PACT Orchestrator...

## S5 POLICY (Non-Negotiables)
- Security: Never expose credentials
- Quality: Tests must pass before merge
- Ethics: No deceptive content
- Delegation: Always delegate to specialists

## PACT AGENT ORCHESTRATION
- When to use each command
- How to delegate effectively
```

### Customization

1. **Add project-specific context** to your project's `CLAUDE.md`
2. **Create project-local skills** in your project's `.claude/skills/` (Claude Code feature)
3. **Create global skills** in `~/.claude/skills/` for use across all projects
4. **Fork the plugin** if you need to modify agents or hooks for your domain

---

## Requirements

- **Claude Code** (the CLI tool): `npm install -g @anthropic-ai/claude-code`
- **Agent Teams enabled** (see [Enabling Agent Teams](#enabling-agent-teams) below)
- **Python 3.9+** (for memory system and hooks)
- **macOS or Linux** (Windows support coming soon)

### Enabling Agent Teams

> **Required since PACT v3.0.** PACT's specialist agents now run as an Agent Team — a coordinated group of Claude Code instances with shared tasks and inter-agent messaging. Agent Teams are experimental in Claude Code and **disabled by default**.

Add the following to your `settings.json` (global `~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

Without this setting, PACT commands like `/PACT:orchestrate` and `/PACT:comPACT` will fail to spawn specialist agents.

> **Note:** Agent Teams have [known limitations](https://code.claude.com/docs/en/agent-teams#limitations) around session resumption, task coordination, and shutdown behavior. See the Claude Code docs for details.

### Optional Dependencies

```bash
# For memory system with embeddings
pip install sqlite-vec

# For n8n workflows
# Requires n8n-mcp MCP server
```

---

## Examples

### Start a New Feature

```
User: I need user authentication with JWT tokens

Claude: I'll use /PACT:orchestrate to coordinate this...

[PREPARE] Researching JWT best practices, library options...
[ARCHITECT] Designing auth flow, token structure, middleware...
[CODE] Backend coder implementing AuthService, middleware...
[TEST] Test engineer verifying login, refresh, edge cases...
```

### Quick Fix

```
User: /PACT:comPACT backend Fix the null check in validateToken

Claude: Invoking backend specialist for focused fix...
[Backend Coder] Fixed null check, added test, verified build passes.
```

### Planning Before Building

```
User: /PACT:plan-mode Design a caching strategy for our API

Claude: [S4 Intelligence Mode] Consulting specialists...
[Preparer] Researching Redis vs Memcached vs in-memory...
[Architect] Designing cache invalidation strategy...
[Database] Considering query patterns for cache keys...

Plan saved to docs/plans/api-caching-plan.md
```

---

## Upgrading from v2.x to v3.0

PACT v3.0 is a **breaking change**. The agent execution model migrated from subagents to **Agent Teams** — a flat team of coordinated Claude Code instances with shared task lists and direct inter-agent messaging.

### What changed

| Aspect | v2.x (Subagents) | v3.0 (Agent Teams) |
|--------|-------------------|---------------------|
| **Execution model** | Subagents within a single session | Independent Claude Code instances per specialist |
| **Communication** | Results returned to orchestrator only | Teammates message each other directly |
| **Task tracking** | Orchestrator-managed | Shared task list with self-coordination |
| **Lifecycle** | Ephemeral (one task, then gone) | Persistent (remain as consultants after their phase) |

### What you need to do

1. **Enable Agent Teams** in your `settings.json` (see [Enabling Agent Teams](#enabling-agent-teams))
2. **Update CLAUDE.md**: Re-copy the orchestrator config from the plugin — the orchestration instructions changed significantly
   ```bash
   # Back up your existing CLAUDE.md first
   cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.bak
   # Then re-copy from the updated plugin
   cp ~/.claude/plugins/cache/pact-marketplace/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md
   ```
   If you have custom content in `~/.claude/CLAUDE.md`, manually merge the updated PACT section (between `<!-- PACT_START -->` and `<!-- PACT_END -->` markers) instead of overwriting.
3. **Restart Claude Code**

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes following PACT principles
4. Run `/PACT:peer-review` for multi-agent code review
5. Submit PR

---

## License

MIT License - See [LICENSE](LICENSE) for details.

---

## Links

- [Claude Code Documentation](https://code.claude.com/docs)
- [Report Issues](https://github.com/ProfSynapse/PACT-prompt/issues)
- [VSM Background](https://en.wikipedia.org/wiki/Viable_system_model)
