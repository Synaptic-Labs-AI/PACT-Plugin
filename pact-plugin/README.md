# PACT Framework Plugin

> **Version**: 3.5.1
> **License**: MIT

VSM-enhanced orchestration framework for AI-assisted software development with Claude Code.

> **Breaking change in v3.0:** PACT now uses [Agent Teams](https://code.claude.com/docs/en/agent-teams) instead of subagents. You must enable Agent Teams in your `settings.json` before using this plugin:
> ```json
> {
>   "env": {
>     "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
>   }
> }
> ```
> Agent Teams are experimental and disabled by default in Claude Code. See the [main README](https://github.com/ProfSynapse/PACT-prompt#upgrading-from-v2x-to-v30) for full upgrade instructions.

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
| **11 Specialist Agents** | Preparer, Architect, Backend/Frontend/Database/DevOps Coders, n8n, Test Engineer, Security Engineer, QA Engineer, Memory Agent |
| **9 Commands** | orchestrate, comPACT, rePACT, plan-mode, imPACT, peer-review, pin-memory, wrap-up, telegram-setup |
| **13 Skills** | Domain knowledge for architecture, coding, testing, security, n8n workflows |
| **Protocols** | VSM-based coordination, algedonic signals, variety management |

## Quick Start

After installing this plugin, use these commands:

```
/PACT:orchestrate <task>     # Full multi-agent workflow
/PACT:comPACT <domain> <task> # Single specialist, light ceremony
/PACT:plan-mode <task>        # Strategic planning before implementation
```

## What's New in v3.0+

- **Agent Teams**: Specialists run as coordinated Claude Code instances with shared tasks and direct messaging (replaces subagent model)
- **Agent Lifecycle Management**: Reuse-vs-spawn decisions, reviewer-to-fixer pipelines, graceful shutdown
- **Persistent Teammates**: Completed-phase agents remain available as consultants for follow-up questions

## Key Features (v2.0+)

- **Variety Management**: Tasks scored on complexity; ceremony scales accordingly
- **Viability Sensing**: Agents emit HALT/ALERT signals for security, data, ethics issues
- **Adaptive Workflow**: From quick fixes to full orchestration based on task complexity
- **Risk-Tiered Testing**: Quality rigor scales with code sensitivity

## Telegram Bridge

PACT includes an optional Telegram integration that lets you interact with Claude Code sessions from your phone. The bridge runs as an opt-in MCP server (via `/PACT:telegram-setup`) and provides four tools:

| Tool | Description |
|------|-------------|
| `telegram_notify` | Send a one-way notification (supports HTML/Markdown formatting) |
| `telegram_ask` | Send a blocking question with optional inline keyboard buttons; supports text and voice replies |
| `telegram_check_replies` | Poll for queued replies to notifications (non-blocking) |
| `telegram_status` | Health check showing connection status, mode, uptime, and feature availability |

**Key capabilities:**
- Session prefix (`[ProjectName]`) on all messages for multi-session support
- Voice note transcription via OpenAI Whisper (optional)
- Inline keyboard buttons for quick-reply options on `telegram_ask`
- Rate limiting and concurrent question caps for safety
- Passive mode (tools available but no automatic notifications) or active mode

### Quick Setup

```
/PACT:telegram-setup
```

This interactive command walks you through creating a Telegram bot, detecting your chat ID, and registering the MCP server. See [telegram-setup.md](commands/telegram-setup.md) for full details.

## Documentation

For full documentation, visit the main repository:
https://github.com/ProfSynapse/PACT-prompt

## Reference

- [pact-protocols.md](protocols/pact-protocols.md) - Source of truth (see granular pact-*.md files for imports)
- [algedonic.md](protocols/algedonic.md) - Emergency signal protocol
- [vsm-glossary.md](reference/vsm-glossary.md) - VSM terminology in PACT context
