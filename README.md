# PACT — Orchestration Harness for Claude Code

> **Stop vibe coding. Start orchestrating.** PACT turns a single Claude Code session into a managed team of specialist AI agents that prepare, design, build, and test your code systematically.

<!-- TODO: Add demo GIF showing PACT orchestrating a real task -->

## The Problem

You ask Claude Code to build a feature. It starts coding immediately — no research, no design, no plan. Halfway through, it loses context and starts guessing. You end up with code that sort of works but wasn't thought through.

This is **vibe coding**: one AI trying to do everything at once, with no structure and no memory.

## The Solution

PACT turns one AI into a coordinated dev team. Instead of a single Claude guessing at everything, **12 specialist agents** each handle what they're best at — research, architecture, implementation, testing — through a systematic **Prepare, Architect, Code, Test** cycle.

| Without PACT | With PACT |
|-------------|-----------|
| AI starts coding immediately | Research and planning happen first |
| Context lost mid-task | Each specialist gets a fresh context window |
| One agent guesses at everything | Dedicated researchers, architects, coders, testers |
| No memory between sessions | Persistent memory system across sessions |
| Same mistakes repeated | Lessons learned are captured and reused |

---

## Quick Start

> **Prerequisite:** PACT requires [Agent Teams](#enabling-agent-teams), which is experimental and disabled by default. Add `"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"` to the `"env"` section of your `~/.claude/settings.json` before installing.

**1. Install the plugin**
```
/plugin marketplace add Synaptic-Labs-AI/PACT-Plugin
/plugin install PACT@pact-plugin
```

**2. Set up the orchestrator** ([detailed options below](#installation))
```bash
cp ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md
```

**3. Allow team file access** (prevents permission prompts during agent operations)

Add team directory access and PACT permission allow rules to your `~/.claude/settings.json` — see [Enabling Agent Teams](#enabling-agent-teams) for the full settings block.

**4. Restart Claude Code and go**
```
/PACT:orchestrate Build user authentication with JWT
```

> See [full installation](#installation) for all options including auto-updates.

### Loading PACT at session start

PACT is delivered through the `--agent` flag — `claude --agent PACT:pact-orchestrator` launches Claude Code with the orchestrator persona loaded directly. Two convenience options:

**Per-project: `.claude/settings.json` convention**

Add the agent setting to your project's `.claude/settings.json` so every session in that repo opens with the orchestrator persona:

```json
{
  "agent": "PACT:pact-orchestrator"
}
```

Plain `claude` in the project root then loads PACT automatically.

**Global: bundled `pact` shell script (recommended for global use)**

The plugin ships a ready-to-use launcher script at `pact-plugin/bin/pact`. Symlink it onto a directory on your `PATH`:

```bash
ln -s "$HOME/.claude/plugins/cache/pact-plugin/PACT/<version>/pact-plugin/bin/pact" \
      "$HOME/.local/bin/pact"
```

(Replace `<version>` with the installed version — check via `ls ~/.claude/plugins/cache/pact-plugin/PACT/`.)

Then `pact` (with any flags `claude` accepts) launches a PACT-loaded session from anywhere. The script is a thin wrapper around `claude --agent PACT:pact-orchestrator "$@"`.

**Upgrade and trust-model notes for the symlink pattern**:
- The symlink target is **version-pinned** — after a plugin upgrade, the old version directory is removed and the symlink dangles. Re-create the symlink after each minor/major upgrade by re-running the `ln -s` above with the new `<version>`. (For automatic upgrade-resilience, wait for the first-class CLI wrapper roadmapped below, or use the shell-function alternative.)
- The symlink **follows plugin updates atomically** — whatever `bin/pact` ships in the next plugin version becomes your `pact` command on next invocation. This is convenient (auto-patching ergonomic improvements) but it means a compromised plugin distribution would auto-execute on next `pact` invocation. Trade-off: symlink follows updates / shell function (or manual copy) is one-shot tamper-evident. Symlink is reasonable as default for the same reason the rest of the plugin tree is — the entire plugin cache is user-trust-bounded — but it's a documented choice, not unstated default.

**Global: alternative `pact()` shell function**

If you prefer not to symlink, add this to your `~/.zshrc` or `~/.bashrc`:

```bash
pact() { claude --agent PACT:pact-orchestrator "$@"; }
```

Same effect as the symlinked script.

> **Roadmap**: A first-class `pact` CLI wrapper with install automation, manpage, and packaging-manager integration is planned for v4.0.x or v4.1.0; the symlink + shell function patterns above are the interim paths.

---

## See It In Action

*Simplified for illustration. Actual output varies by task and project.*

### Building a Feature from Scratch

```
You:   "I need user authentication with JWT tokens"

PACT:  [PREPARE]   Researching JWT best practices, library options, security patterns...
       [ARCHITECT]  Designing auth flow, token structure, middleware, refresh strategy...
       [CODE]       Backend coder implementing AuthService, JWT middleware, token rotation...
       [TEST]       Test engineer verifying login, refresh, expiration, edge cases...

Result: Production-ready auth system — researched, designed, built, and tested.
```

### Quick Fix with a Single Specialist

```
You:   /PACT:comPACT backend Fix the null check in validateToken

PACT:  [Backend Coder] Analyzed the issue, fixed null check, added guard clause,
       verified build passes. Done.
```

### Planning Before Building

```
You:   /PACT:plan-mode Design a caching strategy for our API

PACT:  [Preparer]  Researching Redis vs Memcached vs in-memory options...
       [Architect]  Designing cache invalidation strategy, TTL policies...
       [Database]   Analyzing query patterns for optimal cache keys...

       Plan saved to docs/plans/api-caching-plan.md — ready for your approval.
```

---

## How It Works

### The PACT Cycle

Every task flows through four phases, each handled by the right specialist:

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

The orchestrator manages the cycle, delegating each phase to the appropriate specialist. Simple tasks get light process (`/PACT:comPACT`); complex tasks get the full ceremony (`/PACT:orchestrate`). PACT scales its rigor to match the complexity of the work.

### The Specialist Team

| Agent | What They Do |
|-------|-------------|
| **Preparer** | Research, gather requirements, read docs |
| **Architect** | Design systems, create blueprints, define contracts |
| **Backend Coder** | Implement server-side logic, APIs, middleware |
| **Frontend Coder** | Build user interfaces, client-side logic |
| **Database Engineer** | Design schemas, optimize queries, migrations |
| **DevOps Engineer** | CI/CD, Docker, infrastructure, build systems |
| **n8n Specialist** | Build workflow automations |
| **Test Engineer** | Write and run comprehensive tests |
| **Security Engineer** | Adversarial security code review |
| **QA Engineer** | Runtime verification, exploratory testing |
| **Secretary** | Research assistant, knowledge distiller, context preservation |

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
| `/PACT:telegram-setup` | Set up Telegram notifications | Interact with sessions from mobile |

### comPACT Examples

Target a specific specialist directly:

```bash
/PACT:comPACT backend   Fix the authentication bug
/PACT:comPACT frontend  Add loading spinner to submit button
/PACT:comPACT database  Add index to users.email column
/PACT:comPACT test      Add unit tests for payment module
/PACT:comPACT architect Should we use microservices here?
/PACT:comPACT prepare   Research OAuth2 best practices
```

---

## Features

### Specialist Agents
Eleven agents with distinct expertise — from research to security review. Each gets a fresh context window, so no single agent is overloaded.

### Persistent Memory
A local SQLite database with vector embeddings and graph-linked memories. Decisions, lessons, and context persist across sessions — PACT remembers what worked and what didn't.

### Adaptive Complexity
Tasks are scored on novelty, scope, uncertainty, and risk. Simple tasks get light process; complex tasks get full ceremony with planning, architecture, and multi-agent review.

### Agent Lifecycle
Specialists persist after their phase, available as consultants for follow-up questions. Reviewers become fixers. No wasted context.

### Telegram Bridge (Optional)
Stay connected to your Claude Code sessions from your phone. Get notifications, answer blocking questions, and send voice replies — all from Telegram. Run `/PACT:telegram-setup` to enable.

---

## Under the Hood

PACT is built on the **Viable System Model** (VSM), a cybernetics framework for designing organizations that can adapt and survive. Here's why this design works — each layer handles a distinct concern, so the system stays coherent as complexity grows:

- **S1 (Operations)**: Specialist agents doing the actual work — each autonomous within their domain
- **S2 (Coordination)**: Protocols preventing agents from stepping on each other's work
- **S3 (Control)**: The orchestrator managing current execution — tracking progress, clearing blockers
- **S4 (Intelligence)**: Strategic assessment — is the plan still valid? Should we adapt?
- **S5 (Policy)**: Non-negotiable rules — security, quality, ethics — that no operational pressure overrides

### Hooks (Automation)

| Hook | Trigger | Purpose |
|------|---------|---------|
| `session_init.py` | Session start | Initialize PACT environment, generate team |
| `bootstrap_gate.py` | UserPromptSubmit + PreToolUse | Inject session-start ritual directive on first turn |
| `phase_completion.py` | Session stop | Remind about decision logs |
| `validate_handoff.py` | Agent handoff | Verify output quality |
| `track_files.py` | File edit/write | Track files for memory graph |

*(Selected hooks shown — see [hooks/](pact-plugin/hooks/) for full list)*

### Protocols

Coordination protocols handle agent communication, phase transitions, scope detection, algedonic signals (emergency escalation), and variety management. See the [protocol reference](pact-plugin/protocols/) for details.

### Conversation Theory

PACT uses Gordon Pask's Conversation Theory to ensure shared understanding between agents. Teachback protocols verify that downstream agents correctly understood upstream decisions before proceeding.

---

## Requirements

- **Claude Code** (the CLI tool): `npm install -g @anthropic-ai/claude-code`
- **Agent Teams enabled** (see [Enabling Agent Teams](#enabling-agent-teams) below)
- **Python 3.9+** (for memory system and hooks)
- **macOS or Linux** (Windows support coming soon)

### Enabling Agent Teams

> **Required since PACT v3.0.** PACT's specialist agents run as an Agent Team — a coordinated group of Claude Code instances with shared tasks and inter-agent messaging. Agent Teams are experimental in Claude Code and **disabled by default**.

Add the following to your `settings.json` (global `~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
  "permissions": {
    "additionalDirectories": [
      "~/.claude/teams",
      "~/.claude/pact-sessions"
    ],
    "allow": [
      "Write(~/.claude/agent-memory/**)",
      "Read(~/.claude/agent-memory/**)",
      "Edit(~/.claude/agent-memory/**)",
      "Write(~/.claude/pact-sessions/**)",
      "Read(~/.claude/pact-sessions/**)",
      "Edit(~/.claude/pact-sessions/**)",
      "Write(~/.claude/pact-memory/**)",
      "Read(~/.claude/pact-memory/**)",
      "Edit(~/.claude/pact-memory/**)",
      "Write(~/.claude/pact-telegram/**)",
      "Read(~/.claude/pact-telegram/**)",
      "Edit(~/.claude/pact-telegram/**)"
    ]
  }
}
```

The `env` setting enables Agent Teams. The `permissions.additionalDirectories` entries allow agents to access team coordination files in `~/.claude/teams/` and session journals in `~/.claude/pact-sessions/` without permission prompts. The `permissions.allow` rules prevent recurring prompts for agent memory, session state, and Telegram config file operations.

> **Note:** Bash allow rules are intentionally omitted — they are [fragile](https://docs.anthropic.com/en/docs/claude-code/settings#permission-settings) for commands with arguments. When agents run `mkdir` or `rm` in `~/.claude/` paths, select **"Yes, and always allow from this project"** to add the rule automatically.

Without the `env` setting, PACT commands like `/PACT:orchestrate` and `/PACT:comPACT` will fail to spawn specialist agents.

> **Note:** Agent Teams have [known limitations](https://code.claude.com/docs/en/agent-teams#limitations) around session resumption, task coordination, and shutdown behavior. See the Claude Code docs for details.

### Optional Dependencies

```bash
# For memory system with embeddings
pip install sqlite-vec

# For n8n workflows
# Requires n8n-mcp MCP server
```

---

## Installation

### Option A: Let Claude Set It Up (Easiest)

**Quick version** — give Claude this prompt:

```
Read the PACT setup instructions at https://github.com/Synaptic-Labs-AI/PACT-Plugin/blob/main/README.md
and help me install the PACT plugin with auto-updates enabled.
```

**Step-by-step version** — if you prefer more control, give Claude this instead:

```
Help me install the PACT plugin for Claude Code:

1. Add the marketplace: /plugin marketplace add Synaptic-Labs-AI/PACT-Plugin
2. Install the plugin: /plugin install PACT@pact-plugin
3. Enable auto-updates via /plugin → Marketplaces → pact-plugin → Enable auto-update
4. Set up the orchestrator by appending PACT's CLAUDE.md to my existing ~/.claude/CLAUDE.md
   (or create it if I don't have one)
5. Configure my settings.json per the "Enabling Agent Teams" section of this README
   (env, additionalDirectories, and permission allow rules)
6. Tell me to restart Claude Code
```

### Option B: Manual Installation

**Step 1: Add the marketplace**
```bash
/plugin marketplace add Synaptic-Labs-AI/PACT-Plugin
```

**Step 2: Install the plugin**
```bash
/plugin install PACT@pact-plugin
```

**Step 3: Enable auto-updates**
- Run `/plugin`
- Select **Marketplaces**
- Select **pact-plugin**
- Enable **Auto-update**

**Step 4: Set up the Orchestrator**

The PACT Orchestrator needs to be in your global `CLAUDE.md`:

```bash
# If you DON'T have an existing ~/.claude/CLAUDE.md:
cp ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md

# If you DO have an existing ~/.claude/CLAUDE.md, append PACT to it:
cat ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md >> ~/.claude/CLAUDE.md
```

**Step 5: Allow team file access**

Add the Agent Teams environment variable, team directory access, and PACT permission allow rules to your `~/.claude/settings.json`. See [Enabling Agent Teams](#enabling-agent-teams) for the full settings block.

> **Note:** Merge with any existing keys in your `settings.json` — don't replace the whole file.

**Step 6: Restart Claude Code**
```bash
exit
claude
```

### Option C: Clone for Development

If you want to contribute or customize PACT:

```bash
git clone https://github.com/Synaptic-Labs-AI/PACT-Plugin.git
cd PACT-Plugin
claude
```

### Restart Required

After installing, you **must restart Claude Code**:

1. Type `exit` or close the terminal
2. Run `claude` again

This loads all agents, hooks, and skills properly.

### Verifying Installation

After restart, test with:
```
/PACT:orchestrate Hello, confirm PACT is working
```

You should see the PACT Orchestrator respond.

---

## Skills (16 Modules)

PACT includes 16 skills — 13 domain knowledge modules that load on-demand, plus 3 operational skills (`pact-agent-teams`, `worktree-setup`, `worktree-cleanup`) used internally by the orchestrator.

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

## Telegram Bridge (Optional)

Stay connected to your Claude Code sessions from Telegram. The bridge runs as an opt-in MCP server and provides four tools:

- **`telegram_notify`** -- Send one-way notifications (HTML/Markdown)
- **`telegram_ask`** -- Ask a blocking question with inline keyboard buttons; accepts text or voice replies
- **`telegram_check_replies`** -- Poll for queued replies to notifications (non-blocking)
- **`telegram_status`** -- Health check (connection, uptime, voice availability)

Messages are prefixed with `[ProjectName]` so you can track multiple sessions. Voice replies are transcribed via OpenAI Whisper (optional).

**Setup:** Run `/PACT:telegram-setup` and follow the interactive prompts. See [telegram-setup.md](pact-plugin/commands/telegram-setup.md) for details.

---

## Project Structure

### Plugin Installation (Recommended)

When installed as a plugin, PACT lives in your plugin cache:

```
~/.claude/
├── CLAUDE.md                   # Orchestrator (copy from plugin)
├── plugins/
│   └── cache/
│       └── pact-plugin/
│           └── PACT/
│               └── 4.1.3/     # Plugin version
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
PACT-Plugin/
├── .claude-plugin/
│   └── marketplace.json        # Self-hosted marketplace definition
├── pact-plugin/                # Plugin source (canonical)
│   ├── .claude-plugin/
│   │   └── plugin.json         # Plugin definition
│   ├── agents/                 # 11 specialist agents
│   ├── commands/               # 9 PACT workflow commands
│   ├── skills/                 # 16 skills (13 domain + 3 operational)
│   ├── hooks/                  # Automation hooks
│   ├── protocols/              # Coordination protocols
│   └── CLAUDE.md               # Orchestrator configuration
└── docs/
```

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

## Upgrading from v3.x to v4.0

PACT v4.0 is a **breaking change**. The orchestrator persona delivery model migrated from CLAUDE.md routing → `--agent` flag. Sessions launched without the new flag (or one of the convenience patterns that sets it) will run as default Claude Code, not as the PACT Orchestrator.

### What changed

| Aspect | v3.x (CLAUDE.md routing) | v4.0 (`--agent` flag) |
|--------|---------------------------|------------------------|
| **Persona delivery** | `Skill("PACT:bootstrap")` invoked from CLAUDE.md `PACT_ROUTING` block | Agent body delivered directly via `--agent PACT:pact-orchestrator` |
| **Invocation** | Plain `claude` in a PACT project (CLAUDE.md routing did the work) | `claude --agent PACT:pact-orchestrator` (or settings.json / `pact` script convention) |
| **Bootstrap mechanics** | Multi-step skill chain loaded protocol files at runtime | Persona body inline at session start; protocols loaded lazily on demand |
| **CLAUDE.md routing block** | Required (`PACT_ROUTING` block injected by `session_init`) | Removed — block is stripped on session start during the v4.0.x and v4.1.x deprecation window |
| **Session-start ritual** | Bundled into the bootstrap skill chain (loaded protocols + ran ritual together) | Restored as a ritual-only `/PACT:bootstrap` command + `bootstrap_gate.py` injection (persona delivery is now via `--agent`; the command performs the ritual only) |

### What you need to do

1. **Restore plain-`claude` ergonomics via one of three paths**:
   - **Per-project (recommended for your PACT projects)** — add to your project's `.claude/settings.json`:
     ```json
     {
       "agent": "PACT:pact-orchestrator"
     }
     ```
     Plain `claude` in the project root then auto-loads PACT.
   - **Global (recommended for cross-project use)** — symlink the bundled `pact` script onto your `PATH`:
     ```bash
     ln -s "$HOME/.claude/plugins/cache/pact-plugin/PACT/<version>/pact-plugin/bin/pact" \
           "$HOME/.local/bin/pact"
     ```
     Then `pact` invokes a PACT-loaded session from anywhere. Replace `<version>` with the installed version (`ls ~/.claude/plugins/cache/pact-plugin/PACT/`).
   - **Manual flag (no setup)** — invoke `claude --agent PACT:pact-orchestrator` every time.
2. **Don't be confused by the silent muscle-memory failure**: if you type `claude` in your PACT project from v3.x muscle memory and your `.claude/settings.json` doesn't have the `agent` key set, you'll get default Claude Code without the orchestrator persona. The session will work, just without PACT. Add the settings.json entry once and the muscle memory works again.
3. **Your CLAUDE.md migration is automatic** — no manual cleanup required. Specifically:
   - **Project CLAUDE.md `PACT_ROUTING` block is auto-stripped**: v4.0.x ships an orphan-stripper that removes the now-stale routing block from your project CLAUDE.md on each session start. The stripper sunsets before v4.2.x. (This is the only deletion the upgrade performs.)
   - **Other PACT-managed sections continue unchanged**: `## Current Session` (auto-managed by session_init), `## Retrieved Context`, `## Pinned Context`, and `## Working Memory` (all auto-managed by the pact-memory skill) keep working as in v3.x. Your saved memories, pinned context, and working memory are not touched by the upgrade.
   - **Structural migration is idempotent**: if your project CLAUDE.md uses the v3.x layout (no `PACT_MANAGED_START`/`PACT_MANAGED_END` outer boundary), v4.0.x wraps PACT-managed content in the new boundary structure on first session start. Runs once; subsequent sessions detect the structure is current and skip the migration.
   - **Your global `~/.claude/CLAUDE.md` is user-owned**: PACT does NOT auto-modify the global file. Any custom content you added there manually persists untouched.
4. **Restart Claude Code** after upgrading the plugin.

### Why the change

Empirical investigation (documented in plugin memory chain `4fa2311 → 27aa95e`) found the v3.x bootstrap-via-CLAUDE.md model fragile under context compaction — protocol files loaded by the bootstrap skill weren't reliably restored when the session was compacted, leading to silent governance loss. The `--agent` flag delivers persona content via a different durability tier (system prompt) that survives compaction architecturally rather than relying on lazy reload. Lazy-load fidelity for protocol detail (the orchestrator's pre-commitment + imperative cross-references) was empirically validated in the manual launch-and-isolation runbook before the v4.0.0 release tag.

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
   cp ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md
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
- [Report Issues](https://github.com/Synaptic-Labs-AI/PACT-Plugin/issues)
- [VSM Background](https://en.wikipedia.org/wiki/Viable_system_model)
