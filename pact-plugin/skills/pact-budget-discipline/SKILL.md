---
name: pact-budget-discipline
description: |
  Six orchestrator budget-discipline patterns for context-pressured PACT sessions.
  Use when: long-running session, many files, multiple concurrent specialists,
  or rising context pressure. Covers: swarm-by-default, fixer model selection,
  trivial-task exception, early /park, skip intermediate docs, and
  replacement-not-resuscitation (with secretary/auditor carve-out).
---

# Budget Discipline for PACT Orchestrators

Six orchestrator behaviors for context-pressured sessions. Invoke via
`Skill("PACT:pact-budget-discipline")`.

## When to Invoke This Skill

Invoke at the start of a session when any of the following are true:

- Variety score is ≥ 7 on the initial task or any dispatched phase
- Work is expected to touch many files or span multiple specialists
- Session is expected to be long-running (multiple phases, multiple PRs)
- The orchestrator notices rising context pressure (e.g., specialists
  compacting mid-task, secretary/auditor behind on harvests)
- A pause/park decision is being considered

---

## 1. Swarm-by-default for high-variety tasks

**Rule**: If a task has variety score ≥ 7 AND touches multiple files, decompose
into single-file (or single-concern) slices and dispatch a swarm. Never
dispatch a monolithic agent on a multi-file high-variety task.

**Empirical support**: Monolithic agents on multi-file tasks cost ~3x more
than decomposed swarms, with worse output quality.

**How to apply**:
1. At dispatch time, if variety ≥ 7 AND multi-file, pause before spawning.
2. Decompose the work into slices where each slice is a single file or a
   single concern.
3. Dispatch a specialist per slice in parallel (via Agent Teams concurrent
   dispatch).
4. Preserve slice independence — do NOT let slices share mutable state or
   ordered dependencies if it can be avoided.

**Interaction with discipline #6**: discipline #1 is about the *initial
dispatch* decision — "did I decompose correctly before spawning?" Discipline
#6 is about the *post-dispatch reuse* decision — "even if I dispatched
correctly, should I reuse this completed agent?" Both push toward more,
smaller, fresher agents. Apply both.

---

## 2. Fixer model selection (Sonnet for fixers, Opus for orchestrator/architect)

> **Status**: DEFERRED — awaiting dedicated `pact-fixer` agent. Tracked in
> issue #382.

**Intended rule**: Match model to task complexity. Orchestrator and architects
need Opus's reasoning depth. Fixers and routine workers don't — Sonnet
handles them at significantly lower per-token cost.

**Why deferred**: Requires either a dedicated `pact-fixer` agent with
`model: claude-sonnet-4-6` in frontmatter, or per-dispatch model overrides
(not yet supported by the platform). Tracked in #382.

**Practice-only until codification**: the orchestrator can select
Sonnet-appropriate work patterns (smaller prompts, mechanical fixes, narrow
scope), but cannot yet force model selection.

---

## 3. Aggressive trivial-task exception

**Rule**: The orchestrator handles tasks requiring fewer than ~3 tool calls
directly, without spawning a specialist. Examples: `gh issue create`,
`git push`, `git tag`, single-file reads to answer a question, simple grep
operations. Each skipped spawn saves 50–70K of baseline overhead (per #361).

**Empirical support**: The exception exists in CLAUDE.md but is
inconsistently practiced. Sessions routinely create 30+ teammates when several
could be skipped.

**How to apply**:
- Before reaching for `Task(...)`, ask: "Can I do this in ≤ 3 tool calls?"
- If yes, AND it's not application code (see CLAUDE.md "What Is Application
  Code?"), do it directly.
- If the thought "I know exactly how to do it and it's not application code"
  appears, that's the signal to apply the exception.

**Not an override of delegation**: this does NOT license the orchestrator to
write application code. It covers non-code operational tasks only (git,
gh CLI, trivial reads/greps). Application code still delegates.

---

## 4. `/park` (or equivalent) earlier rather than later

**Rule**: When a natural break point appears — PR merged, phase complete,
user pause — park the session immediately rather than letting it grow until
forced compaction. Forced compaction triggers the post-compaction
degraded-state cost documented in #364.

**Empirical support**: Forced compaction mid-task produces silent stalls and
degraded recovery state. Voluntary parking avoids this entirely.

**Natural break points** (park candidates):
- Just after a PR merges (and cleanup completes)
- Just after a CODE phase completes and commits land green
- After a long TEST phase produces a clean run
- When the user signals a pause ("let's stop here for now", "I need to step
  away", etc.)
- When the orchestrator notices it is nearing its own context budget

**How to apply**: invoke `/PACT:park` (or equivalent pause command) as soon
as the break point appears. Do not defer "one more small task" — the cost of
a forced compaction dwarfs the cost of parking and resuming.

---

## 5. Skip intermediate documentation artifacts unless they'll be referenced downstream

**Rule**: PACT's PREPARE and ARCHITECT phases produce documents in
`docs/preparation/` and `docs/architecture/`. These are useful when downstream
phases will reference them. They are NOT useful when the work is small enough
that the next phase can hold the context directly. Skip the intermediate docs
in those cases — they cost agent context to produce and rarely earn back the
cost.

**Decision criteria**: Generate the doc only if:
- (a) variety score ≥ 11, OR
- (b) the work spans multiple sessions, OR
- (c) a stakeholder will read it (including future-you at a point when the
  current context has been compacted away).

Otherwise, the next phase can use the orchestrator's in-memory context.

**How to apply**:
1. At the start of PREPARE or ARCHITECT, check the decision criteria.
2. If none apply, instruct the preparer/architect to report findings inline
   via HANDOFF (and task metadata) instead of producing a file.
3. If one applies, generate the doc as normal.

---

## 6. Replacement-not-resuscitation for context-heavy agents

**Rule**: When an agent has already executed a complex task (variety ≥ 7,
multi-file scope, architecture-heavy, or any task that required ≥ 5
significant tool invocations), do NOT reuse them for a subsequent task even
if the domain overlaps. Spawn a fresh agent instead.

**Anticipate the capacity problem from the task shape, don't react to it
from measured state.** By the time "near capacity" is measurable, the agent
is minutes from a compaction stall.

**Empirical support**: A single agent dispatched for 8 commits (variety 9)
compacted 2-3 times; a swarm of focused sub-agents would have avoided it.
The CLAUDE.md Reuse-vs-Spawn matrix reflects this proactive trigger.

**Scope**: Applies to reuse *across tasks*, not turn-to-turn continuation
within a single task. The rule fires when a task completes and the
orchestrator decides whether to reuse the agent for the *next* task.

### Exception — singular-per-session roles (secretary, auditor)

Discipline #6 does **NOT** apply to the `secretary` and `auditor` roles.
Both should remain **singular per session** — a single secretary instance
and a single auditor instance persist from session start through wrap-up,
handling all work in their respective roles continuously.

**Rationale**: These roles have light context-load profiles (observation,
coordination, distillation — not implementation) and their value comes from
**session-long continuity**. A secretary who observed the full session
produces richer consolidation and catches cross-phase dedup. An auditor who
saw earlier commits catches drift patterns in later ones. Spawning fresh
instances fragments this accumulated knowledge.

**Empirical support**: Over-applying discipline #6 to the secretary resulted
in 4 instances re-reading session context from scratch, producing fragmented
consolidation. A single persistent secretary produces richer output.

**Summary**:
- **Coders/implementation specialists** → spawn fresh when prior task was complex.
- **Secretary and auditor** → singular-per-session. Do NOT replace until
  wrap-up or pause.

---

## Cross-cutting: Measuring Context Pressure

Watch for these proactive indicators:

- **Task shape**: variety score, file count, architecture-heavy flags
- **Specialist compaction**: if any teammate has compacted, assume others are close
- **Secretary/auditor lag**: harvests queueing up behind other work
- **Session duration**: multi-hour sessions accumulate cruft regardless of per-task variety
- **Own context**: slower recall of earlier decisions = park soon

When two or more indicators fire, re-evaluate dispatch decisions against all
six disciplines.
