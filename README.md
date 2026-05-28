# PACT — Orchestration Harness for Claude Code

> **Stop vibe coding. Start orchestrating.** PACT turns a single Claude Code session into a managed team of specialist AI agents that prepare, design, build, and test your code systematically.

## The Problem

You ask Claude Code to build a feature. It starts coding immediately — no research, no design, no plan. Halfway through, it loses context and starts guessing. You end up with code that sort of works but wasn't thought through.

This is **vibe coding**: one AI trying to do everything at once, with no structure and no memory.

## The Solution

PACT turns one AI into a coordinated dev team. Instead of a single Claude guessing at everything, **12 specialist agents** plus an orchestrator handle what they're best at — research, architecture, implementation, concurrent audit, testing — through a systematic **Prepare, Architect, Code, Test** cycle.

| Without PACT | With PACT |
| --- | --- |
| AI starts coding immediately | Research and planning happen first |
| Context lost mid-task | Each specialist gets a fresh context window |
| One agent guesses at everything | Dedicated researchers, architects, coders, auditors, testers |
| No memory between sessions | Persistent memory system across sessions |
| No durable workflow state | Append-only session journal survives compaction and task GC |
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

```
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

```
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

```
pact() { claude --agent PACT:pact-orchestrator "$@"; }
```

Same effect as the symlinked script.

> **Roadmap**: A first-class `pact` CLI wrapper with install automation, manpage, and packaging-manager integration is planned for v4.0.x or v4.1.0; the symlink + shell function patterns above are the interim paths.

---

## See It In Action

*Simplified for illustration. Actual output varies by task and project.*

### Building a feature from scratch

```
You:   "I need user authentication with JWT tokens"

PACT:  [PREPARE]   Researching JWT best practices, library options, security patterns...
       [ARCHITECT]  Designing auth flow, token structure, middleware, refresh strategy...
       [CODE]       Backend coder implementing AuthService, JWT middleware, token rotation...
                    Auditor observing concurrently for architecture drift...
       [TEST]       Test engineer verifying login, refresh, expiration, edge cases...

Result: Production-ready auth system — researched, designed, built, audited, tested.
```

### Quick fix with a single specialist

```
You:   /PACT:comPACT backend Fix the null check in validateToken

PACT:  [Backend Coder] Analyzed the issue, fixed null check, added guard clause,
       verified build passes. Done.
```

### Planning before building

```
You:   /PACT:plan-mode Design a caching strategy for our API

PACT:  [Preparer]  Researching Redis vs Memcached vs in-memory options...
       [Architect]  Designing cache invalidation strategy, TTL policies...
       [Database]   Analyzing query patterns for optimal cache keys...

       Plan saved to docs/plans/api-caching-plan.md — ready for your approval.
```

---

## How It Works

### The PACT cycle

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
│                           ↑                                 │
│                       Auditor observes concurrently         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

The orchestrator manages the cycle, delegating each phase to the appropriate specialist. Simple tasks get light process (`/PACT:comPACT`); complex tasks get the full ceremony (`/PACT:orchestrate`). PACT scales its rigor to match the complexity of the work.

### The specialist team

| Agent | What They Do |
| --- | --- |
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
| **Auditor** | Concurrent observation during CODE — catches architecture drift before it compounds |
| **Secretary** | Research assistant, knowledge distiller, context preservation |

> **About the Auditor**: The Auditor is architecturally distinct from every other specialist. It runs **concurrently** during the CODE phase rather than sequentially, performing observation cycles via `git diff` and Read (not messaging) to minimize coder disruption. It emits structural signals — GREEN (on track), YELLOW (worth noting), RED (intervene now) — based on diff-verifiable evidence, not coder self-attestation. Concurrent audit during code generation is uncommon in agentic dev tools, and the Auditor's structural verification discipline ensures GREEN signals trace back to actual diff content rather than agreeable prose. See [`protocols/pact-audit.md`](pact-plugin/protocols/pact-audit.md) for the full protocol.

---

## Commands

| Command | Purpose | When to Use |
| --- | --- | --- |
| `/PACT:orchestrate` | Full multi-agent workflow | New features, complex tasks |
| `/PACT:comPACT` | Single specialist, light process | Quick fixes, focused tasks |
| `/PACT:plan-mode` | Planning consultation (no code) | Before complex implementations |
| `/PACT:rePACT` | Nested PACT cycle for sub-tasks | Complex sub-problems during CODE |
| `/PACT:imPACT` | Triage when blocked | Hit a blocker, need help deciding |
| `/PACT:peer-review` | Commit, PR, multi-agent review | Ready to merge |
| `/PACT:bootstrap` | Session-start ritual after `--agent` loads | Invoked automatically; manual override available |
| `/PACT:pause` | Consolidate memory, persist state, shut down teammates | PR open but not ready to merge; end of day |
| `/PACT:wrap-up` | Full end-of-session cleanup (worktree, tasks, session decision) | Ending a work session |
| `/PACT:pin-memory` | Pin critical context permanently | Gotchas, key decisions to preserve |
| `/PACT:prune-memory` | Interactively evict an existing pin | When `pin_caps_gate` blocks a new pin |
| `/PACT:start-pending-scan` | Arm a 5-minute cron to scan for pending completion-authority work | Long-running parallel dispatch |
| `/PACT:scan-pending-tasks` | Cron-fired scan body (not user-invoked) | Fires automatically while armed |
| `/PACT:stop-pending-scan` | Disarm the pending-task scan | Returning to normal cadence |
| `/PACT:telegram-setup` | Set up Telegram notifications | Interact with sessions from mobile |

### comPACT examples

Target a specific specialist directly:

```
/PACT:comPACT backend   Fix the authentication bug
/PACT:comPACT frontend  Add loading spinner to submit button
/PACT:comPACT database  Add index to users.email column
/PACT:comPACT test      Add unit tests for payment module
/PACT:comPACT architect Should we use microservices here?
/PACT:comPACT prepare   Research OAuth2 best practices
```

---

## Features

### Specialist agents

Twelve agents plus an orchestrator, with distinct expertise — from research to security to concurrent audit. Each gets a fresh context window, so no single agent is overloaded.

### Persistent memory

A local SQLite database with vector embeddings and graph-linked memories. Decisions, lessons, and context persist across sessions — PACT remembers what worked and what didn't. Memory updates support scalar replacement and additive list merge, so `lessons_learned` grows monotonically rather than overwriting.

### Durable workflow state

A per-session append-only journal records every phase transition, agent dispatch, handoff, and commit. The journal survives context compaction, Claude Code task garbage collection, and crashes — so workflow state is recoverable even when the task system isn't. See [Session journal](#session-journal) below.

### Adaptive complexity

Tasks are scored on novelty, scope, uncertainty, and risk. Simple tasks get light process; complex tasks get full ceremony with planning, architecture, and multi-agent review. Calibration data feeds back into pattern-adjusted scoring (Bateson's Learning II), so variety estimates improve as the system sees more of your codebase.

### Concurrent audit

The Auditor runs alongside coders during the CODE phase, observing via `git diff` rather than messaging. It catches architecture drift while it's cheap to fix, before it has to be undone in TEST or review.

### Agent lifecycle

Specialists persist after their phase, available as consultants for follow-up questions. Reviewers become fixers. No wasted context.

### Telegram notifications

Stay connected to your Claude Code sessions from your phone. Get notifications, answer blocking questions, and send voice replies — all from Telegram. Run `/PACT:telegram-setup` to enable.

---

## Safety & integrity

PACT contains explicit safety machinery for agentic-system failure modes that most tools handle implicitly (or not at all). The three principles below show up throughout the protocols and hooks.

### Integrity non-negotiable

The orchestrator's S5 policy treats integrity as sacrosanct. From [`protocols/pact-s5-policy.md`](pact-plugin/protocols/pact-s5-policy.md):

- Never fabricate user input
- Never generate `Human:` turns
- Never assume user consent — messages arriving between system events (teammate shutdowns, idle notifications) are not user input

Irreversible actions — merge, force push, branch deletion, PR close — require `AskUserQuestion` rather than acting on bare text. The narrow exception is post-merge branch cleanup (e.g., `git branch -d` in `worktree-cleanup`), which is authorized by the merge itself and does not require separate confirmation.

If a non-negotiable would be violated, work stops. No operational pressure justifies crossing the boundary.

### Cron-Origin Distinction

The pending-task scan system uses Claude Code's cron scheduler to wake the orchestrator periodically and check for completion-authority work on disk. This creates a subtle problem: the prompt body firing a scheduled skill looks like user-typed text but isn't.

From [`commands/scan-pending-tasks.md`](pact-plugin/commands/scan-pending-tasks.md):

> Cron-fire turns are NOT user consent. The platform cron scheduler invokes this skill at 5-minute intervals while a `/PACT:scan-pending-tasks` cron is registered. The prompt body that fires this skill is harness-origin text. Downstream consent-gated decisions MUST NOT proceed on the basis of a cron-fire turn.

Consent-gated decisions (merge, push, destructive bash, plan approval, version bump, force-completion) defer to the next user-typed turn or to an explicit `AskUserQuestion` checkpoint. The scan body itself avoids these actions by construction — it only reads filesystem, calls the canonical acceptance pair, or emits nothing.

The scan replaced an earlier `INBOX_GREW` wake mechanism that admitted a hallucination-cascade failure mode: the lead inferring "the teammate must have sent me something" and generating a response to imagined content before the platform's content-delivery channel caught up. The Cron-Origin Distinction generalizes: **harness-origin text is not user consent, even when it looks like user-typed text.**

### Falsifiable-by-construction patterns

Several PACT protocols are designed so that the system literally cannot fabricate the conditions for an action — only verify them on disk.

**Two-call atomic acceptance pair** — From [`protocols/pact-completion-authority.md`](pact-plugin/protocols/pact-completion-authority.md): when accepting teammate work, the orchestrator MUST send a wake-signal `SendMessage` FIRST, then write `TaskUpdate(status="completed")`. The ordering is load-bearing — the wake must be on disk before the status flip fires, so the lifecycle gate's PostToolUse scan finds the paired wake. Reversed ordering produces false-positive `completion_no_paired_send` warnings even when the pair is structurally correct.

**First-spawn HARD-STOP verification** — From the orchestrator persona (§11 Agent Teams Dispatch): after the first specialist spawn in a session, the teammate's first message MUST demonstrate access to `TaskList`, `TaskUpdate`, and `SendMessage`. If any tool is reported missing, this is **not degraded mode** and **not something to work around** — the dispatch was malformed (typically `Task(...)` was used instead of `Agent(...)`, or `name=` / `team_name=` was omitted). The orchestrator stops the teammate, corrects the dispatch shape, and re-spawns. The teammate cannot self-recover from a malformed spawn.

**Structural verification discipline** — From [`protocols/pact-audit.md`](pact-plugin/protocols/pact-audit.md): before emitting GREEN on a structural acceptance criterion, the Auditor MUST verify the claim against `git diff` ground truth. Pattern-matching on HANDOFF prose, commit messages, or coder self-attestation is not sufficient evidence — four internally-consistent layers of prose can all be wrong together. The diff is evidence; prose is retrieval aid.

---

## Under the Hood

PACT is built on the **Viable System Model** (VSM), a cybernetics framework for designing organizations that can adapt and survive. Each layer handles a distinct concern so the system stays coherent as complexity grows:

- **S1 (Operations)**: Specialist agents doing the actual work — each autonomous within their domain
- **S2 (Coordination)**: Protocols preventing agents from stepping on each other's work
- **S3 (Control)**: The orchestrator managing current execution — tracking progress, clearing blockers
- **S3\* (Audit)**: Independent ground-truth observation — approximated through the Auditor and risk-tiered testing
- **S4 (Intelligence)**: Strategic assessment — is the plan still valid? Should we adapt?
- **S5 (Policy)**: Non-negotiable rules — security, quality, ethics, integrity — that no operational pressure overrides

For canonical VSM terminology in the PACT context, see [`reference/vsm-glossary.md`](pact-plugin/reference/vsm-glossary.md).

### Hooks (automation)

PACT registers hooks across 11 Claude Code event surfaces, including `SessionStart`, `UserPromptSubmit`, `PreCompact`, `PostCompact`, `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, `SessionEnd`, `TaskCompleted`, and `TeammateIdle`. Selected hooks:

| Hook | Trigger | Purpose |
| --- | --- | --- |
| `session_init.py` | SessionStart | Initialize PACT environment, generate team, restore prior session |
| `bootstrap_gate.py` | PreToolUse | Inject session-start ritual directive on first turn |
| `validate_handoff.py` | SubagentStop | Verify HANDOFF output quality |
| `track_files.py` | PostToolUse (Edit/Write) | Track files for memory graph |
| `agent_handoff_emitter.py` | TaskCompleted | Write `agent_handoff` event to session journal |
| `dispatch_gate.py` | PreToolUse (Agent) | Catch malformed teammate spawns at dispatch time |
| `pin_caps_gate.py` | PreToolUse (Edit/Write) | Enforce caps on CLAUDE.md pinned-memory section |
| `postcompact_archive.py` | PostCompact | Archive pre-compaction state for recovery |
| `wake_inbox_drain.py` | UserPromptSubmit | Drain pending inbox events |
| `auditor_reminder.py` | SubagentStart + PostToolUse:Agent | Surface auditor presence/skip decisions |

See [`pact-plugin/hooks/hooks.json`](pact-plugin/hooks/hooks.json) for the full registration matrix; the [`hooks/`](pact-plugin/hooks/) directory contains the 29 top-level hooks plus `shared/` utilities and a `refresh/` subsystem for transcript replay and checkpoint reconstruction.

### Protocols

Twenty-two protocols cover agent communication, phase transitions, scope detection and decomposition, completion authority, teachback, algedonic signals, variety management, audit, and state recovery. They are loaded lazily by the orchestrator via cross-references — only the protocols relevant to the current situation enter the context window. See [`protocols/pact-protocols.md`](pact-plugin/protocols/pact-protocols.md) for the index, and [`protocols/`](pact-plugin/protocols/) for individual files.

### Session journal

PACT maintains a per-session append-only JSONL journal at `~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl`. Implementation in [`pact-plugin/hooks/shared/session_journal.py`](pact-plugin/hooks/shared/session_journal.py).

- **Schema-versioned events** with per-type required-field validation. Event types include `session_start`, `session_end`, `session_paused`, `session_consolidated`, `variety_assessed`, `phase_transition`, `checkpoint`, `agent_dispatch`, `agent_handoff`, `commit`, `s2_state_seeded`, `review_dispatch`, `review_finding`, `remediation`, `pr_ready`, `teardown_request`, `scan_armed` (see [`pact-plugin/hooks/shared/session_journal.py`](pact-plugin/hooks/shared/session_journal.py) for the complete registry of 20 types)
- **Append-only with `fcntl.flock(LOCK_EX)`** advisory locking around short-write loops to handle concurrent hooks + orchestrator CLI calls safely
- **Tail-window reverse scan** (32 KB) for fast `read-last` operations; falls back to full-file slurp when needed
- **Best-effort durability** — no `fsync` per write (hot path), but cross-process visibility is immediate after lock release
- **Single-host scope** — advisory locks don't cross machines, which is fine because `pact-sessions` is per-host already

The journal is the durable backbone of workflow state. It survives context compaction, Claude Code task garbage collection, `TeamDelete`, and crashes. The wrap-up command harvests journal events to pact-memory before session close; the journal persists for 30 days afterward as a recovery window. Paused sessions are exempt from TTL cleanup.

Knowledge memory (pact-memory) and workflow state (session journal) are intentionally separate: pact-memory captures durable lessons and decisions; the journal captures where the workflow is right now and what artifacts have been produced. The [`pact-state-recovery.md`](pact-plugin/protocols/pact-state-recovery.md) protocol documents the recovery hierarchy across both.

### Conversation Theory

PACT uses Gordon Pask's Conversation Theory to ensure shared understanding between agents. Every specialist dispatch creates a Task A (teachback) + Task B (work) pair with `blockedBy=[A]`. The teammate writes `metadata.teachback_submit` restating their understanding, idles on `awaiting_lead_completion`, and waits. The orchestrator reviews the teachback against the dispatched task, then accepts via a two-call atomic pair (wake-signal `SendMessage` first, then `TaskUpdate(A, completed)`), which auto-unblocks Task B. Misunderstandings surface before they propagate. Full protocol in [`pact-ct-teachback.md`](pact-plugin/protocols/pact-ct-teachback.md).

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

### Optional dependencies

```
# For memory system with embeddings
pip install sqlite-vec

# For n8n workflows
# Requires n8n-mcp MCP server
```

---

## Installation

### Option A: Let Claude set it up (easiest)

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

### Option B: Manual installation

**Step 1: Add the marketplace**

```
/plugin marketplace add Synaptic-Labs-AI/PACT-Plugin
```

**Step 2: Install the plugin**

```
/plugin install PACT@pact-plugin
```

**Step 3: Enable auto-updates**

- Run `/plugin`
- Select **Marketplaces**
- Select **pact-plugin**
- Enable **Auto-update**

**Step 4: Set up the Orchestrator**

The PACT Orchestrator needs to be in your global `CLAUDE.md`:

```
# If you DON'T have an existing ~/.claude/CLAUDE.md:
cp ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md ~/.claude/CLAUDE.md

# If you DO have an existing ~/.claude/CLAUDE.md, append PACT to it:
cat ~/.claude/plugins/cache/pact-plugin/PACT/*/CLAUDE.md >> ~/.claude/CLAUDE.md
```

**Step 5: Allow team file access**

Add the Agent Teams environment variable, team directory access, and PACT permission allow rules to your `~/.claude/settings.json`. See [Enabling Agent Teams](#enabling-agent-teams) for the full settings block.

> **Note:** Merge with any existing keys in your `settings.json` — don't replace the whole file.

**Step 6: Restart Claude Code**

```
exit
claude
```

### Option C: Clone for development

If you want to contribute or customize PACT:

```
git clone https://github.com/Synaptic-Labs-AI/PACT-Plugin.git
cd PACT-Plugin
claude
```

### Restart required

After installing, you **must restart Claude Code**:

1. Type `exit` or close the terminal
2. Run `claude` again

This loads all agents, hooks, and skills properly.

### Verifying installation

After restart, test with:

```
/PACT:orchestrate Hello, confirm PACT is working
```

You should see the PACT Orchestrator respond.

---

## Skills (20 modules)

PACT ships 20 skill modules — domain knowledge that loads on-demand, plus operational skills used internally by the orchestrator.

### PACT phase skills

| Skill | Triggers On |
| --- | --- |
| `pact-prepare-research` | Research, requirements, API exploration |
| `pact-architecture-patterns` | System design, C4 diagrams, patterns |
| `pact-coding-standards` | Clean code, error handling, conventions |
| `pact-testing-strategies` | Test pyramid, coverage, mocking |
| `pact-security-patterns` | Auth, OWASP, credential handling |

### Operational skills

| Skill | Purpose |
| --- | --- |
| `pact-agent-teams` | Teammate-side protocol for Agent Teams operations |
| `pact-teachback` | Teachback submission, idle, rejection, and revise mechanics |
| `pact-handoff-harvest` | Secretary harvest of completed-task HANDOFFs into pact-memory |
| `pact-memory` | Memory CRUD CLI, save-vs-update dedup, graph-enhanced search |
| `worktree-setup` | Create isolated git worktree for a feature |
| `worktree-cleanup` | Tear down a worktree after merge |
| `request-more-context` | Specialist request for additional context from the orchestrator |
| `telegram-guide` | Telegram bridge usage from inside the team |

### n8n workflow skills

| Skill | Triggers On |
| --- | --- |
| `n8n-workflow-patterns` | Workflow architecture, webhooks |
| `n8n-node-configuration` | Node setup, field dependencies |
| `n8n-expression-syntax` | Expressions, `$json`, `$node` |
| `n8n-code-javascript` | JavaScript in Code nodes |
| `n8n-code-python` | Python in Code nodes |
| `n8n-validation-expert` | Validation errors, debugging |
| `n8n-mcp-tools-expert` | MCP tool usage |

---

## Memory System

PACT includes a persistent memory system for cross-session learning:

```
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
- Additive list merge on update (lessons grow monotonically; scalars replace)
- Prefix-based ID lookup (7+ chars, case-insensitive; ambiguous prefix refused)
- Pinned memories in CLAUDE.md gated by `pin_caps_gate` with eviction via `/PACT:prune-memory`
- Auto-prompts to save after significant work

**Storage:** `~/.claude/pact-memory/` (persists across projects)

---

## Telegram Bridge (Optional)

Stay connected to your Claude Code sessions from Telegram. The bridge runs as an opt-in MCP server and provides four tools:

- **`telegram_notify`** — Send one-way notifications (HTML/Markdown)
- **`telegram_ask`** — Ask a blocking question with inline keyboard buttons; accepts text or voice replies
- **`telegram_check_replies`** — Poll for queued replies to notifications (non-blocking)
- **`telegram_status`** — Health check (connection, uptime, voice availability)

Messages are prefixed with `[ProjectName]` so you can track multiple sessions. Voice replies are transcribed via OpenAI Whisper (optional).

**Setup:** Run `/PACT:telegram-setup` and follow the interactive prompts. See [telegram-setup.md](pact-plugin/commands/telegram-setup.md) for details.

---

## Project Structure

### Plugin installation (recommended)

When installed as a plugin, PACT lives in your plugin cache:

```
~/.claude/
├── CLAUDE.md                   # Orchestrator (copy from plugin)
├── plugins/
│   └── cache/
│       └── pact-plugin/
│           └── PACT/
│               └── 4.3.4/      # Plugin version
│                   ├── agents/
│                   ├── commands/
│                   ├── skills/
│                   ├── hooks/
│                   ├── protocols/
│                   └── reference/
├── pact-memory/                # Memory database (shared)
│   ├── memory.db
│   └── models/
│       └── all-MiniLM-L6-v2.gguf
└── pact-sessions/              # Session journals (per-host)
    └── {slug}/
        └── {session_id}/
            └── session-journal.jsonl
```

### Your project

```
your-project/
├── CLAUDE.md                   # Project-specific config (optional)
└── docs/
    ├── plans/                  # Implementation plans
    ├── architecture/           # Design documents
    ├── decision-logs/          # Implementation decisions
    └── preparation/            # Research outputs
```

### Development clone

If you cloned this repo for development/contribution:

```
PACT-Plugin/
├── .claude-plugin/
│   └── marketplace.json        # Self-hosted marketplace definition
├── pact-plugin/                # Plugin source (canonical)
│   ├── .claude-plugin/
│   │   └── plugin.json         # Plugin definition
│   ├── agents/                 # 12 specialist agents + 1 orchestrator
│   ├── commands/               # 15 PACT workflow commands
│   ├── skills/                 # 20 skill modules
│   ├── hooks/                  # Lifecycle automation (29 top-level + shared/ + refresh/)
│   ├── protocols/              # 22 coordination protocols
│   ├── reference/              # VSM glossary
│   ├── telegram/               # Telegram bridge MCP server
│   ├── templates/              # Settings and environment-model templates
│   └── tests/                  # 147 test files
└── docs/
```

---

## Configuration

### CLAUDE.md

The `CLAUDE.md` file configures the orchestrator. Key sections:

```
# MISSION
Act as PACT Orchestrator...

## S5 POLICY (Non-Negotiables)
- Security: Never expose credentials
- Quality: Tests must pass before merge
- Ethics: No deceptive content
- Integrity: Never fabricate user input or assume consent
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
| --- | --- | --- |
| **Persona delivery** | `Skill("PACT:bootstrap")` invoked from CLAUDE.md `PACT_ROUTING` block | Agent body delivered directly via `--agent PACT:pact-orchestrator` |
| **Invocation** | Plain `claude` in a PACT project (CLAUDE.md routing did the work) | `claude --agent PACT:pact-orchestrator` (or settings.json / `pact` script convention) |
| **Bootstrap mechanics** | Multi-step skill chain loaded protocol files at runtime | Persona body inline at session start; protocols loaded lazily on demand |
| **CLAUDE.md routing block** | Required (`PACT_ROUTING` block injected by `session_init`) | Removed — block is stripped on session start during the v4.0.x–v4.2.14 deprecation window |
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

```
ln -s "$HOME/.claude/plugins/cache/pact-plugin/PACT/<version>/pact-plugin/bin/pact" \
      "$HOME/.local/bin/pact"
```
Then `pact` invokes a PACT-loaded session from anywhere. Replace `<version>` with the installed version (`ls ~/.claude/plugins/cache/pact-plugin/PACT/`).

  - **Manual flag (no setup)** — invoke `claude --agent PACT:pact-orchestrator` every time.
2. **Don't be confused by the silent muscle-memory failure**: if you type `claude` in your PACT project from v3.x muscle memory and your `.claude/settings.json` doesn't have the `agent` key set, you'll get default Claude Code without the orchestrator persona. The session will work, just without PACT. Add the settings.json entry once and the muscle memory works again.
3. **Your CLAUDE.md migration is mostly automatic** — one manual cleanup step for direct v3.x upgraders. Specifically:
  - **Project CLAUDE.md `PACT_ROUTING` block — manual removal for direct v3.x → v4.3.0+ upgraders**: v4.0.x through v4.2.14 shipped an orphan-stripper that auto-removed the stale `<!-- PACT_ROUTING_START ... --> ... <!-- PACT_ROUTING_END -->` block from your project CLAUDE.md on each session start. The stripper retired in v4.3.0 (no v4.2.15 was released; the deprecation-window-closing version is v4.3.0). If you ran any v4.0.x, v4.1.x, or v4.2.0–v4.2.14 session, the block is already gone. If you upgrade directly from v3.x to v4.3.0+ without an interim session, delete the `<!-- PACT_ROUTING_START ... -->` ... `<!-- PACT_ROUTING_END -->` block from your project CLAUDE.md manually before first session start.
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
| --- | --- | --- |
| **Execution model** | Subagents within a single session | Independent Claude Code instances per specialist |
| **Communication** | Results returned to orchestrator only | Teammates message each other directly |
| **Task tracking** | Orchestrator-managed | Shared task list with self-coordination |
| **Lifecycle** | Ephemeral (one task, then gone) | Persistent (remain as consultants after their phase) |

### What you need to do

1. **Enable Agent Teams** in your `settings.json` (see [Enabling Agent Teams](#enabling-agent-teams))
2. **Update CLAUDE.md**: Re-copy the orchestrator config from the plugin — the orchestration instructions changed significantly

```
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
- [Conversation Theory (Pask)](https://en.wikipedia.org/wiki/Conversation_theory)
