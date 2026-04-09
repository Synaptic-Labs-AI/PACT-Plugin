---
name: pact-budget-discipline
description: |
  Orchestrator budget-discipline behaviors for context-pressured PACT sessions.
  Use when: session is expected to be long-running, touches many files, involves
  multiple concurrent specialists, or the orchestrator notices rising context
  pressure. Provides six discipline patterns: swarm-by-default, fixer model
  selection, trivial-task exception, early /park, skipping intermediate
  documentation artifacts, and replacement-not-resuscitation for context-heavy
  agents (with secretary/auditor carve-out).
---

# Budget Discipline for PACT Orchestrators

This skill codifies the six orchestrator behaviors from issue #366. It is
**lazy-loaded** — no per-spawn cost. Invoke it via `Skill("pact-budget-discipline")`
when a session is context-pressured, long-running, or multi-specialist.

These disciplines were observed empirically during the PR #350 review session
(2026-04-06) and the PR #374 session (2026-04-08). Each has direct measurable
backing from the session that surfaced it.

> **Related issues**: #361 (per-spawn baseline reduction), #380 (tool curation
> follow-up), #381 (auto-memory externalization follow-up), #382 (fixer-agent
> model selection follow-up for discipline #2), #166 (original Reuse-vs-Spawn
> matrix), #364 (compaction hook cost), #365 (teammate lifecycle automation).

## When to Invoke This Skill

Invoke `Skill("pact-budget-discipline")` at the start of a session when any of
the following are true:

- Variety score is ≥ 7 on the initial task or any dispatched phase
- Work is expected to touch many files or span multiple specialists
- Session is expected to be long-running (multiple phases, multiple PRs)
- The orchestrator notices rising context pressure (e.g., specialists
  compacting mid-task, secretary/auditor behind on harvests)
- A pause/park decision is being considered

The skill content is ~5.5K tokens when loaded. Zero cost otherwise.

---

## 1. Swarm-by-default for high-variety tasks

**Rule**: If a task has variety score ≥ 7 AND touches multiple files, decompose
into single-file (or single-concern) slices and dispatch a swarm. Never
dispatch a monolithic agent on a multi-file high-variety task.

**Empirical support**: PR #350 review session, 2026-04-06. Two consecutive
monolithic full-PR bughunter dispatches (r8, r9) consumed ~360K tokens combined
and produced minimal output. The 5-agent r10 swarm consumed ~125K tokens and
produced 19 actionable findings. The successful swarm cost ~35% of the failed
monoliths for the same task — a 3× cost win on the failure case.

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

**Intended rule**: Match model selection to task complexity. The orchestrator
and architectural specialists need Opus's reasoning depth. Fixers, cleanup
specialists, and routine workers don't — Sonnet handles them at significantly
lower per-token cost.

**Empirical support**: PR #350 session's r11 + r12 fixer swarms (8 agents)
used Opus to apply known fixes from known findings to known files. That's
mechanical work that doesn't benefit from Opus's reasoning depth.

**Why deferred**: The codification requires either (a) a dedicated
`pact-fixer` agent definition with `model: claude-sonnet-4-6` in its
frontmatter, or (b) per-dispatch `model:` overrides at the platform level. The
platform does not currently support per-dispatch model overrides, and no
fixer-role agent exists yet. Creating the agent is a new-feature change, not
a context-reduction change — out of scope for #361.

**Practice-only until codification**: the orchestrator can still manually
select Sonnet-appropriate work patterns (smaller prompts, mechanical
application of known fixes, narrow scope), but cannot yet force model
selection. Follow-up work is tracked in #382.

---

## 3. Aggressive trivial-task exception

**Rule**: The orchestrator handles tasks requiring fewer than ~3 tool calls
directly, without spawning a specialist. Examples: `gh issue create`,
`git push`, `git tag`, single-file reads to answer a question, simple grep
operations. Each skipped spawn saves 50–70K of baseline overhead (per #361).

**Empirical support**: This exception is already documented in CLAUDE.md but
is inconsistently practiced. The exception exists; the discipline of actually
invoking it is what matters. Per-session data from PR #350 showed 33+
teammates created, several of which could have been skipped.

**How to apply**:
- Before reaching for `Task(...)`, ask: "Can I do this in ≤ 3 tool calls?"
- If yes, AND it's not application code (see CLAUDE.md "What Is Application
  Code?"), do it directly.
- If the thought "I know exactly how to do it and it's not application code"
  appears, that's the signal to apply the exception.

**Not an override of delegation**: this does NOT license the orchestrator to
write application code. It covers non-code operational tasks only (git,
gh CLI, trivial reads/greps). Application code still delegates.

**Tool curation follow-up**: Reducing the per-dispatch tool schema footprint
(so that even when spawning IS needed, the baseline is lower) is tracked
separately as #380.

---

## 4. `/park` (or equivalent) earlier rather than later

**Rule**: When a natural break point appears — PR merged, phase complete,
user pause — park the session immediately rather than letting it grow until
forced compaction. Forced compaction triggers the post-compaction
degraded-state cost documented in #364.

**Empirical support**: PR #374 session's `r9-bughunter-functional` teammate
demonstrated the post-compaction failure mode firsthand. Their work was
interrupted by forced compaction; the recovery state produced silent stalls.
Earlier voluntary parking would have avoided this entirely.

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

**Connection**: #364 will eventually fix the platform-level compaction cost;
this discipline avoids hitting it in the meantime. Auto-memory architecture
changes are tracked separately in #381 — once those land, parking becomes
even cheaper because the session state at pause is smaller.

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

**Why this discipline matters**: producing a ~300-line architecture document
costs ~5–10K tokens of agent context for writing, formatting, and
self-review. If no downstream agent reads the file, that cost is pure waste.
In-memory HANDOFF context flows to the next phase via Task metadata at
negligible cost.

---

## 6. Replacement-not-resuscitation for context-heavy agents

**Rule**: When an agent has already executed a complex task (variety ≥ 7,
multi-file scope, architecture-heavy, or any task that required ≥ 5
significant tool invocations), do NOT reuse them for a subsequent task even
if the domain overlaps. Spawn a fresh agent instead.

The existing Reuse-vs-Spawn matrix (originally added by #166) uses *"context
near capacity"* as the spawn trigger — that threshold is too late. By the
time the orchestrator can measure "near capacity," the agent is already
minutes away from a compaction stall. **Anticipate the capacity problem from
the task shape, don't react to it from measured state.**

**Empirical support**: PR #374 session (team `pact-cd65d80f`, 2026-04-08).
A single `backend-coder` was dispatched for 8 commits spanning `database.py`,
`models.py`, `memory_api.py`, `cli.py`, SKILL.md, `memory-patterns.md`, and 4
version files (variety 9, multi-file high-risk contract-reversal work). The
coder compacted 2–3 times during execution; the test-engineer and auditor
each compacted at least once. Each compaction cost several minutes of stall
time per #364. A swarm of smaller specialists — one per commit-pair group —
would have avoided the compaction cycles entirely. Each sub-agent would have
received a clean ~200K budget for a focused <60K-token task.

**Refinement to the CLAUDE.md Reuse-vs-Spawn matrix**: replace the reactive
"Agent's context near capacity from prior work → Spawn new" row with a
proactive anticipation rule. The matrix edit is included as part of this
PR's commit 4.

**Scope of the rule**: The refinement applies to reuse *across tasks within
a session*, not to turn-to-turn continuation within a single task. An agent
mid-task continues its own work — the rule fires when a task completes and
the orchestrator is deciding whether to reuse the completed agent for the
next task.

### Exception — singular-per-session roles (secretary, auditor)

Discipline #6 does **NOT** apply to the `secretary` and `auditor` roles.
Both should remain **singular per session** — a single secretary instance
and a single auditor instance persist from session start through wrap-up,
handling all work in their respective roles continuously.

**Rationale**: These two roles have fundamentally different context-load
profiles from coders and other implementation specialists. They don't do
heavy architectural thinking, don't write application code, and don't hold
large implementation state in working memory. Their work is observation,
coordination, and distillation — all light-context activities that can run
session-long without approaching the capacity threshold that triggers
compaction stalls.

More importantly, **their value comes from session-long continuity**. The
secretary is the session's institutional knowledge layer: every harvest,
every query answered, every memory distillation is cumulative. A single
secretary who has observed the full session's history answers queries more
accurately, catches dedup opportunities that span phases, and produces
consolidation harvests that weave together earlier and later discoveries.
Spawning fresh secretaries for each harvest trigger fragments knowledge
across instances and defeats the role's purpose — the Nth secretary doesn't
know what the first N-1 observed. Similarly, the auditor's value compounds
across commits: an auditor who saw commits 1–2 and brings that context to
commit 3's audit catches drift patterns and cross-commit invariant
violations that a fresh-per-commit auditor cannot.

**Empirical support**: PR #374 violated this exception by over-applying
discipline #6 to the secretary role. Four secretary instances were spawned
(`secretary`, `secretary-harvest`, `secretary-harvest-2`, `secretary-pause`),
each re-reading the session context from scratch. The consolidation pass
especially suffered: a secretary present for the full session would have
produced a richer, more deduplicated consolidation than a fresh agent
reading the session journal cold.

**Corrected rule summary**:
- **Coders and other implementation specialists with heavy context load** →
  replacement-not-resuscitation per the main discipline #6 rule above. Spawn
  fresh when the prior task was complex.
- **Secretary and auditor** → *singular-per-session*, rely on their inherent
  light context load. Do NOT replace until the session itself ends via
  wrap-up or pause. The main secretary handles ALL harvest triggers
  (post-ARCHITECT, post-CODE, at peer-review dispatch, at wrap-up
  consolidation) as continuous work. The main auditor handles ALL
  commit-boundary audits and the final pre-peer-review sweep.

---

## Cross-cutting: Measuring Context Pressure

Budget discipline is most valuable when applied *before* hitting the wall.
Watch for these proactive indicators:

- **Task shape**: variety score, file count, architecture-heavy flags — these
  predict capacity consumption before it happens
- **Specialist compaction events**: if any teammate has compacted during the
  current session, assume the rest are close behind
- **Secretary/auditor lag**: if harvests are queueing up behind other work,
  the orchestrator is accumulating unprocessed state
- **Session duration**: long sessions (multi-hour, multi-phase) accumulate
  cruft even if no single task is high-variety
- **Own context**: when the orchestrator itself notices slower recall of
  earlier decisions, that is an indicator to park soon

When two or more indicators fire simultaneously, invoke this skill (if not
already loaded) and re-evaluate dispatch decisions against all six
disciplines.
