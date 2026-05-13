# PACT — Orchestration Harness for Claude Code

> **Version**: 4.2.2

Turn a single Claude Code session into a managed team of specialist AI agents that prepare, design, build, and test your code systematically.

> **Breaking change in v4.0:** PACT now delivers the orchestrator persona via the `--agent PACT:pact-orchestrator` flag (or settings.json / `pact` script convention) instead of CLAUDE.md routing. See the [v4.0 upgrade guide](https://github.com/Synaptic-Labs-AI/PACT-Plugin#upgrading-from-v3x-to-v40) for details — also requires [Agent Teams enabled](https://github.com/Synaptic-Labs-AI/PACT-Plugin#enabling-agent-teams) per v3.0+.

## Install in 30 Seconds

```bash
/plugin marketplace add Synaptic-Labs-AI/PACT-Plugin
/plugin install PACT@pact-plugin
cp ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md
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

Then restart Claude Code. Requires [Agent Teams enabled](https://github.com/Synaptic-Labs-AI/PACT-Plugin#enabling-agent-teams) and the `--agent` flag wired up — see [Loading PACT at session start](https://github.com/Synaptic-Labs-AI/PACT-Plugin#upgrading-from-v3x-to-v40) for the three convenience patterns (per-project `.claude/settings.json`, the `pact` shell wrapper, or manual `claude --agent PACT:pact-orchestrator`).

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

## What's New in v4.0+

- **`--agent` flag persona delivery**: Orchestrator persona ships at the system-prompt tier via `--agent PACT:pact-orchestrator`, durable under context compaction (replaces v3.x CLAUDE.md routing)
- **Lazy-load protocols**: Persona body cross-references protocols on demand instead of bootstrapping them all up front, reducing baseline token cost
- **Restored session-start ritual** (v4.1): Scaled-down `/PACT:bootstrap` command + `bootstrap_gate.py` injection re-establish the first-turn ritual under the new delivery model
- **Communication Charter**: Async-at-idle-boundary delivery model formalized for inter-agent SendMessage mechanics

## Configuration

Environment variables that tune hook behavior:

| Variable | Default | Allowed values | Effect |
|---|---|---|---|
| `PACT_DISPATCH_INLINE_MISSION_MODE` | `warn` | `warn` / `deny` / `shadow` | Disposition of the dispatch-gate inline-mission heuristic (flags dispatchers inlining mission text into `prompt=` instead of using the canonical "check TaskList" form). `warn` emits an advisory `additionalContext`; `deny` blocks the spawn (flip only after empirically confirming `additionalContext` injection is reliable under PreToolUse — see `pact-plugin/hooks/dispatch_gate.py` for the matcher-fidelity discussion); `shadow` journals only — the trigger is observable but neither WARNs nor DENYs (calibration / first-session safety net). The other dispatch-gate rules (name/team presence, name validation, specialist registry, session-team match, member uniqueness, task-assignment) are unaffected by this env-var. Unknown values fall back to `warn`. |

## Full Documentation

For installation options, detailed features, examples, and technical reference:
**[github.com/Synaptic-Labs-AI/PACT-Plugin](https://github.com/Synaptic-Labs-AI/PACT-Plugin)**

## Reference

- [Protocols](protocols/) — Coordination, scope detection, algedonic signals
- [Algedonic Signals](protocols/algedonic.md) — Emergency escalation protocol
- [Communication Charter](protocols/pact-communication-charter.md) — Async-at-idle-boundary inter-agent delivery model
- [Completion Authority](protocols/pact-completion-authority.md) — Lead-only completion + Task A+B dispatch shape
- [State Recovery](protocols/pact-state-recovery.md) — Resume + recovery semantics across sessions
- [S5 Policy](protocols/pact-s5-policy.md) — Non-negotiable rules layer (security, quality, ethics)
- [S4 Tension](protocols/pact-s4-tension.md) — Strategic-vs-operational tension management
- [VSM Glossary](reference/vsm-glossary.md) — Viable System Model terminology in PACT context

## License

MIT — See [LICENSE](../LICENSE)
