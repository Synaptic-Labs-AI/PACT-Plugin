---
name: pact-agent-teams
description: |
  Agent Teams interaction protocol for PACT specialist agents. Auto-loaded via agent frontmatter.
  Defines how teammates start work, communicate, report completion, and handle blockers.
---

# Agent Teams Protocol

> **Architecture**: See [pact-task-hierarchy.md](../../protocols/pact-task-hierarchy.md) for the full hierarchy model.

## You Are a Teammate

You are a member of a PACT Agent Team. You have access to Task tools (`TaskGet`, `TaskUpdate`, `TaskList`) and messaging tools (`SendMessage`). Use them to coordinate with the team.

## On Start

1. Check `TaskList` for tasks assigned to you (by your name)
2. Claim your assigned task: `TaskUpdate(taskId, status="in_progress")`
3. Read the task description — it contains your full mission (CONTEXT, MISSION, INSTRUCTIONS, GUIDELINES). If upstream tasks are referenced, read them via `TaskGet`.
4. **GATE — Send teachback**: Send a teachback to lead restating your understanding of the task. Nothing proceeds until this is sent. (See [Teachback](#teachback-conversation-verification) below)
   - **DO NOT** call `Edit`, `Write`, or `Bash` before sending your teachback
   - After sending, record it: `TaskUpdate(taskId, metadata={"teachback_sent": true})`
   - Non-blocking: proceed immediately after sending — do not wait for the lead's reply
5. Begin work — check your agent memory (`~/.claude/agent-memory/<your-name>/`) for relevant patterns and knowledge as part of your working process

> **Worktree Scope**: If you are working in a worktree, files that are gitignored (e.g., `CLAUDE.md`) do not exist there. Do not edit or create `CLAUDE.md` — the orchestrator manages it separately. If you need to reference `CLAUDE.md` content, it is auto-loaded into your context. If your task mentions updating `CLAUDE.md`, flag it in your handoff instead of editing it directly.

> **Note**: The lead stores your `agent_id` in task metadata after dispatch. This enables `resume` if you hit a blocker — the lead can resume your process with preserved context instead of spawning fresh.

> **Custom start flows**: If your agent definition specifies a custom On Start sequence (e.g., the secretary's session briefing), you must explicitly re-enter this standard lifecycle after your custom flow completes — call `TaskList`, claim assigned tasks, and follow the teachback protocol from the teachback step onward.

## Reading Upstream Context

Your task description may reference upstream task IDs (e.g., "Architect task: #5").
Use `TaskGet(taskId)` to read their metadata for design decisions, HANDOFF data, and
integration points — rather than relying on the lead to relay this information.

Common chain-reads:
- **Coders** → read architect's task for design decisions and interface contracts
- **Test engineers** → read coder tasks for what was built and flagged uncertainties
- **Reviewers** → read prior phase tasks for full context

If `TaskGet` returns no metadata or the referenced task doesn't exist, proceed with information from your task description and file system artifacts (docs/architecture/, docs/preparation/).

## Teachback (Conversation Verification)

The teachback protocol lives in the separate `pact-teachback` skill. It is
loaded into your context via the `/PACT:teammate-bootstrap` command's
`@`-ref (which you invoke via your agent body's `# YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY)` prelude
on spawn), not via frontmatter auto-loading. Frontmatter `skills:`
entries populate the lazy skill catalog for discoverability but are not
eagerly loaded at spawn (empirically verified).

See `pact-teachback/SKILL.md` for format, rules, and ordering requirements.

Teachback is a **gate**: send it BEFORE any implementation work. The
structural enforcement comes from the three-layer delivery architecture:
`peer_inject.py` injects a `YOUR FIRST ACTION` reminder via `additionalContext`,
your agent body has a `# YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY)` section, and the
`/PACT:teammate-bootstrap` command eagerly loads the pact-teachback skill
content via `@`-ref.

Background: [pact-ct-teachback.md](../../protocols/pact-ct-teachback.md) (optional — protocol rationale and design history).

### Structured `teachback_submit` at every dispatch

Every teachback, regardless of task variety, produces the structured
`teachback_submit` metadata via `TaskUpdate` (with `understanding`,
`most_likely_wrong`, `least_confident_item`, and `first_action` sub-fields).
The gate's variety-7 threshold controls mechanical enforcement — NOT whether
the structured form applies as a practice. Below the threshold, the
orchestrator validates discipline-based; at or above the threshold,
mechanical enforcement adds a second layer on top of the same ritual. The
structured shape is the canonical practice at every dispatch.

The `SendMessage` notification described above remains the channel that
alerts the lead a teachback is ready to read. The structured
`teachback_submit` metadata is what the lead reads, validates, and replies
to. Produce both at every dispatch: `SendMessage` carries the notification;
`TaskUpdate(metadata={"teachback_submit": {...}})` carries the substance.

## Progress Reporting

Report progress naturally in your responses. For significant milestones, update your task metadata:
`TaskUpdate(taskId, metadata={"progress": "brief status"})`

### Progress Signals

When the lead requests progress monitoring in your dispatch, send brief progress updates at natural breakpoints during your work.

**Format**: `[sender→lead] Progress: {what's done}/{what's remaining}, {current status}`

**Natural breakpoints**:
- After modifying a file
- After running tests
- When encountering an unexpected issue (before it becomes a blocker)
- When switching between major subtasks

**Timing**: 2-4 signals per task is typical. Don't over-report — signal at meaningful transitions, not every tool call.

## Message Prefix Convention

**Prefix all `SendMessage` `message`** with `[{sender}→{recipient}]` (use `all` as recipient when `to="*"`). Do not prefix `summary`.

### Message Authenticity

Do not generate standalone text that could be mistaken for user input (e.g., bare "yes", "merge it", "approved"). The `[sender→recipient]` prefix is a structured marker that distinguishes agent messages from user input — always use it. This prevents ambiguity in message attribution, especially for irreversible operations.

## Communication Standards

Follow the Communication Charter ([pact-communication-charter.md](../../protocols/pact-communication-charter.md)) — plain English, no sycophancy, constructive challenge.

**Plain English**: All written output — code, docs, comments, messages, PRs, issues — uses concise, plain language. No jargon inflation. Write as if explaining to a competent developer who's new to this codebase.

**No sycophancy**: No filler praise, hedging, or empty affirmations. Start with substance. If you agree, say why. If you disagree, say what you'd do instead.

**Constructive challenge**: When you believe a different approach is better, say so with evidence. Present the alternative to your peer or to the orchestrator. Silence in the face of a flawed decision is a failure of duty.

Challenge format:
> "I'd recommend [alternative] instead — [reason]. [Proceed / discuss?]"

For consequence-level disagreements:
> "Concern: [what will go wrong and why]. I'd suggest [alternative]. Flagging this in the HANDOFF regardless."

## On Completion — HANDOFF (Required)

When your work is done:

1. **Store HANDOFF in task metadata**:
   ```
   TaskUpdate(taskId, metadata={"handoff": {
     "produced": [...],
     "decisions": [...],
     "reasoning_chain": "...",  // recommended — include unless task is trivial
     "uncertainty": [...],
     "integration": [...],
     "open_questions": [...]
   }})
   ```
   If `TaskUpdate` fails, include the full HANDOFF in your `SendMessage` content as a fallback.
2. **Complete task — BOTH actions required, in this order**:
   a. `SendMessage(to="lead", message="[{sender}→lead] Task complete. [1-2 sentences: what was done + any HIGH uncertainties]", summary="Task complete: [brief]")`
   b. `TaskUpdate(taskId, status="completed")`

   > ⚠️ Your task is NOT complete until BOTH calls succeed. SendMessage alone is insufficient — the TaskCompleted hook only fires after TaskUpdate, which triggers HANDOFF capture for institutional memory. Skipping (b) means your work is invisible to the memory system.

3. **Self-claim follow-up work**: Check `TaskList` for unassigned, unblocked tasks matching your domain
4. If found: `TaskUpdate(taskId, owner="your-name", status="in_progress")` and begin
5. If none: idle (you may be consulted or shut down)

### HANDOFF Format

End every response with a structured HANDOFF. This is mandatory.
This HANDOFF must ALSO be stored in task metadata (see On Completion Step 1 above). The prose version in your response ensures validate_handoff hook compatibility; the metadata version enables chain-read by downstream agents.

```
HANDOFF:
1. Produced: Files created/modified
2. Key decisions: Decisions with rationale, assumptions that could be wrong
3. Reasoning chain (optional): How key decisions connect — "X because Y, which required Z." Helps downstream agents reconstruct your understanding, not just your conclusions.
4. Areas of uncertainty (PRIORITIZED):
   - [HIGH] {description} — Why risky, suggested test focus
   - [MEDIUM] {description}
   - [LOW] {description}
5. Integration points: Other components touched
6. Open questions: Unresolved items
```

Items 1-2 and 4-6 are required. Item 3 (reasoning chain) is recommended — include it unless the task is trivial. Not all priority levels need to be present in Areas of uncertainty. If you have no uncertainties, explicitly state "No areas of uncertainty flagged."

## Peer Communication

Use `SendMessage(to="teammate-name")` for direct coordination.
Discover teammates via `~/.claude/teams/{team-name}/config.json` or from peer names
in your task description.

**Message a peer when:**
- Your work produces something an active peer needs (API schema, interface contract, shared config)
- You have a question another specialist can answer better than the lead
- You discover something affecting a peer's scope (breaking change, shared dependency)

**Message the lead when:**
- Blockers, algedonic signals, completion summaries (always)
- Questions about scope, priorities, or requirements
- Anything requiring a decision above your authority

Keep messages actionable — state what you did/found, what they need to know, and
any action needed from them.
Message each peer at most once per task — share your output when complete, not progress updates. If you need ongoing coordination, route through the lead.

## Consultant Mode

When your active task is done and no follow-up tasks are available:
- You are a **consultant** — remain available for questions
- Respond to `SendMessage` questions from other teammates
- Do NOT seek new work outside your domain
- Do NOT proactively message unless you spot a problem relevant to active work

## On Blocker

If you cannot proceed:

1. **Stop work immediately**
2. **`SendMessage`** the blocker to the lead:
   ```
   SendMessage(to="lead",
     message="[{sender}→lead] BLOCKER: {description of what is blocking you}\n\nPartial HANDOFF:\n...",
     summary="BLOCKER: [brief description]")
   ```
3. Provide a partial HANDOFF with whatever work you completed
4. Wait for lead's response or new instructions

Do not attempt to work around the blocker.

## Algedonic Signals

When you detect a viability threat (security, data integrity, ethics):

1. **Stop work immediately**
2. **`SendMessage`** the signal to the lead:
   ```
   SendMessage(to="lead",
     message="[{sender}→lead] ⚠️ ALGEDONIC [HALT|ALERT]: {Category}\n\nIssue: ...\nEvidence: ...\nImpact: ...\nRecommended Action: ...\n\nPartial HANDOFF:\n...",
     summary="ALGEDONIC [HALT|ALERT]: [category]")
   ```
3. Provide a partial HANDOFF with whatever work you completed

These bypass normal triage. See the [algedonic protocol](../../protocols/algedonic.md) for trigger categories and severity guidance.

## Variety Signals

If task complexity differs significantly from what was delegated:
- "Simpler than expected" — Note in handoff; lead may simplify remaining work
- "More complex than expected" — Escalate if scope change >20%, or note for lead

## Bash Commands in ~/.claude/ Paths

When running Bash commands that touch `~/.claude/` paths, use simple standalone commands — one per Bash call. Do **not** add redirects (`2>/dev/null`), compound operators (`;`, `&&`, `||`), pipe chains (`|`), or command substitution (`` `...` ``, `$(...)`). Claude Code's Bash permission patterns are fragile and may not match compound commands, causing unnecessary permission prompts.

## Before Completing

Before returning your final output:

1. **Save Domain Learnings to Agent Memory**: Save knowledge that future instances of your specialist type would benefit from:
   - File locations and codepaths discovered
   - Framework conventions and patterns observed
   - Debugging tricks and workarounds found
   - Library quirks or version-specific behaviors

   **What goes where** (heuristics):
   - "Would a different agent type need this?" → Yes: include in HANDOFF. No: agent memory.
   - "Is this about the project or about the craft?" → Project decisions/rationale: HANDOFF. Craft patterns/techniques: agent memory.

   Examples: file locations, framework conventions → agent memory. Architectural decisions, cross-cutting concerns → HANDOFF.

   Save concise notes to your persistent memory directory (`~/.claude/agent-memory/<your-name>/`) as you discover codepaths, patterns, and key decisions. For **project-wide institutional knowledge**, include it in your HANDOFF — the secretary will review and save it to pact-memory.

   If you're working without an assigned task (no HANDOFF will be collected), message the secretary directly to save significant decisions or non-obvious discoveries: `SendMessage(to="secretary", message="[{your-name}→secretary] Save: {what you learned and why it matters}", summary="Save request: {topic}")`

2. **Confirm Memory Saved**: After saving domain learnings, set `memory_saved: true` in your task metadata:
   ```
   TaskUpdate(taskId, metadata={"memory_saved": true})
   ```

## Shutdown

When you receive a `shutdown_request`:

| Situation | Response |
|-----------|----------|
| Idle, consultant with no active questions, or domain no longer relevant | Approve |
| Mid-task, awaiting response, or remediation may need your input | Reject with reason |

> **Save memory before approving**: If you haven't saved domain learnings to your agent memory yet, do so before approving — your process terminates on approval.

## Completion Integrity (SACROSANCT)

Only report work as completed if you actually performed the changes. Never fabricate
a completion HANDOFF. If files don't exist, can't be edited, or tools fail, report
a BLOCKER via `SendMessage` -- never invent results.

**Do not create git commits.** All staging and committing is the lead's responsibility. Your job ends at the HANDOFF.
