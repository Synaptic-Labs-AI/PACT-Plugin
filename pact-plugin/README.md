# PACT Framework Plugin

> **Version**: 3.0.0
> **License**: MIT

VSM-enhanced orchestration framework for AI-assisted software development with Claude Code. PACT uses Agent Teams for multi-agent orchestration, spawning specialist teammates that communicate via SendMessage for coordinated development workflows.

## Prerequisites

- **Claude Code** CLI installed and configured
- **Agent Teams** enabled (experimental feature). Enable via Claude Code settings before using PACT.
- **Delegate Mode** (optional, recommended): Press Shift+Tab to enable Delegate Mode, which restricts the orchestrator to coordination-only tools. This adds platform-level enforcement on top of PACT's convention-based delegation rules.

## Installation

### Option 1: Let Claude Set It Up (Easiest)

Give Claude this prompt:

```
Read the PACT setup instructions at https://github.com/ProfSynapse/PACT-prompt/blob/main/README.md
and help me install the PACT plugin with auto-updates enabled.
```

### Option 2: Manual Installation

```bash
# 1. Add the marketplace
/plugin marketplace add ProfSynapse/PACT-prompt

# 2. Install the plugin
/plugin install PACT@pact-marketplace

# 3. Enable auto-updates
# Go to /plugin → Marketplaces → pact-marketplace → Enable auto-update

# 4. Set up the Orchestrator
# If you DON'T have ~/.claude/CLAUDE.md:
cp ~/.claude/plugins/cache/pact-marketplace/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md

# If you DO have ~/.claude/CLAUDE.md, append PACT to it:
cat ~/.claude/plugins/cache/pact-marketplace/PACT/*/CLAUDE.md >> ~/.claude/CLAUDE.md

# 5. Restart Claude Code
# (Permissions for background agents are auto-merged on first session start)
exit
claude
```

### Option 3: Local Development

```bash
/plugin marketplace add /path/to/PACT-prompt
/plugin install PACT@pact-marketplace
```

### Updating

- **Auto-update enabled**: Updates happen automatically on startup
- **Manual**: `/plugin marketplace update pact-marketplace`

### Verify Installation

After restart, test with:
```
/PACT:orchestrate Hello, confirm PACT is working
```

---

## What's Included

| Component | Description |
|-----------|-------------|
| **8 Specialist Agents** | Preparer, Architect, Backend/Frontend/Database Coders, n8n, Test Engineer, Memory Agent (spawned as teammates) |
| **7 Commands** | orchestrate, comPACT, plan-mode, imPACT, peer-review, pin-memory, wrap-up |
| **13 Skills** | Domain knowledge for architecture, coding, testing, security, n8n workflows |
| **Protocols** | VSM-based coordination, algedonic signals, variety management |

## Quick Start

After installing this plugin, use these commands:

```
/PACT:orchestrate <task>     # Full multi-agent workflow
/PACT:comPACT <domain> <task> # Single specialist, light ceremony
/PACT:plan-mode <task>        # Strategic planning before implementation
```

## Key Features (v3.0)

- **Agent Teams**: Specialists spawned as teammates via TeamCreate/SendMessage for real-time coordination
- **Variety Management**: Tasks scored on complexity; ceremony scales accordingly
- **Viability Sensing**: Agents emit HALT/ALERT signals for security, data, ethics issues
- **Adaptive Workflow**: From quick fixes to full orchestration based on task complexity
- **Risk-Tiered Testing**: Quality rigor scales with code sensitivity

## Documentation

For full documentation, visit the main repository:
https://github.com/ProfSynapse/PACT-prompt

## Reference

- pact-protocols.md - Source of truth (see granular pact-*.md files for imports)
- `algedonic.md` - Emergency signal protocol
- `vsm-glossary.md` - VSM terminology in PACT context
