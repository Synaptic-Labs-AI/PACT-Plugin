# PACT — Orchestration Harness for Claude Code

> **Version**: 3.17.8

Turn a single Claude Code session into a managed team of specialist AI agents that prepare, design, build, and test your code systematically.

> **Breaking change in v3.0:** PACT now uses [Agent Teams](https://code.claude.com/docs/en/agent-teams) instead of subagents. You must [enable Agent Teams](https://github.com/ProfSynapse/PACT-prompt#enabling-agent-teams) in your `settings.json`. See the [upgrade guide](https://github.com/ProfSynapse/PACT-prompt#upgrading-from-v2x-to-v30) for details.

## Install in 30 Seconds

```bash
/plugin marketplace add ProfSynapse/PACT-prompt
/plugin install PACT@pact-marketplace
cp ~/.claude/plugins/cache/pact-marketplace/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md
```

Then add `~/.claude/teams` and `~/.claude/pact-sessions` to your `additionalDirectories` and PACT allow rules in `~/.claude/settings.json` to prevent permission prompts during agent operations:

```json
{
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

> **Note:** Bash allow rules are intentionally omitted — they are [fragile](https://docs.anthropic.com/en/docs/claude-code/settings#permission-settings) for commands with arguments. When agents run `mkdir` or `rm` in `~/.claude/` paths, select **"Yes, and always allow from this project"** to add the rule automatically.

Then restart Claude Code. Requires [Agent Teams enabled](https://github.com/ProfSynapse/PACT-prompt#enabling-agent-teams).

## What You Get

- **11 Specialist Agents** — Preparer, Architect, Backend/Frontend/Database/DevOps Coders, n8n, Test/Security/QA Engineers, Secretary
- **9 Commands** — From full orchestration to quick single-specialist fixes
- **16 Skills** — On-demand domain knowledge for architecture, coding, testing, security, n8n, plus operational skills
- **Persistent Memory** — SQLite + vector embeddings for cross-session learning
- **Adaptive Complexity** — Light process for simple tasks, full ceremony for complex ones

## Quick Start

```
/PACT:orchestrate <task>          # Full multi-agent workflow
/PACT:comPACT <domain> <task>     # Self-contained tasks, no PACT phases
/PACT:plan-mode <task>            # Strategic planning before implementation
```

## What's New in v3.0+

- **Agent Teams**: Specialists run as coordinated Claude Code instances with shared tasks and direct messaging
- **Persistent Teammates**: Completed-phase agents remain available as consultants
- **Conversation Theory**: Teachback protocols ensure shared understanding between agents

## Full Documentation

For installation options, detailed features, examples, and technical reference:
**[github.com/ProfSynapse/PACT-prompt](https://github.com/ProfSynapse/PACT-prompt)**

## Reference

- [Protocols](protocols/) — Coordination, scope detection, algedonic signals
- [Algedonic Signals](protocols/algedonic.md) — Emergency escalation protocol
- [VSM Glossary](reference/vsm-glossary.md) — Viable System Model terminology in PACT context

## License

MIT — See [LICENSE](../LICENSE)
