# PACT Protocols (Lean Reference)

> **Purpose**: Minimal protocols for PACT workflows. Agents reference this when needed, not memorized.
>
> **Design principle**: One-liners in prompts, details here.
>
> **Theoretical basis**: Structure informed by Stafford Beer's Viable System Model (VSM). See [vsm-glossary.md](../reference/vsm-glossary.md) for full terminology.
>
> **VSM Quick Reference**: S1=Operations (specialists), S2=Coordination (conflict resolution), S3=Control (orchestrator execution), S4=Intelligence (planning/adaptation), S5=Policy (governance/user authority).

---

## S5 Policy Layer (Governance)

The policy layer defines non-negotiable constraints and provides escalation authority. All other protocols operate within these boundaries.

### Non-Negotiables (SACROSANCT)

These rules are **never** overridden by operational pressure:

| Category | Rule | Rationale |
|----------|------|-----------|
| **Security** | No credentials in code; validate all inputs; sanitize outputs | Prevents breaches, injection attacks |
| **Quality** | No known-broken code merged; tests must pass | Maintains system integrity |
| **Ethics** | No deceptive outputs; no harmful content | Aligns with responsible AI principles |
| **Context** | Don't clutter main context with implementation details | Offload heavy lifting to sub-agents; preserves orchestrator capacity |
| **Delegation** | Orchestrator never writes application code | Maintains role boundaries |
| **User Approval** | Never merge PRs without explicit user authorization | User controls their codebase |
| **Integrity** | Never fabricate user input or assume user consent | Prevents unauthorized actions from unverified input |

> **Integrity — Irreversible Actions**: Use `AskUserQuestion` for merge, force push, branch deletion, and PR close. Do not act on bare text for these operations — messages between system events (shutdowns, idle notifications) may not be genuine user input. **Exception**: Post-merge branch cleanup (e.g., `git branch -d` in worktree-cleanup) is authorized by the merge itself and does not require separate confirmation.

**If a rule would be violated**: Stop work, report to user. These are not trade-offs—they are boundaries.

### Delegation Enforcement

**Application code** (orchestrator must delegate):
- Source files (`.py`, `.ts`, `.js`, `.rb`, `.go`, etc.)
- Test files (`.spec.ts`, `.test.js`, `test_*.py`)
- Scripts (`.sh`, `Makefile`, `Dockerfile`)
- Infrastructure (`.tf`, `.yaml`, `.yml`)
- App config (`.env`, `.json`, `config/`)

**Not application code** (orchestrator may edit):
- AI tooling (`CLAUDE.md`, `.claude/`)
- Documentation (`docs/`)
- Git config (`.gitignore`)
- IDE settings (`.vscode/`, `.idea/`)

**Tool Checkpoint**: Before `Edit`/`Write`:
1. STOP — Is this application code?
2. Yes → Delegate | No → Proceed | Uncertain → Delegate

**Recovery Protocol** (if you catch yourself mid-violation):
1. Stop immediately
2. Revert uncommitted changes (`git checkout -- <file>`)
3. Delegate to appropriate specialist
4. Note the near-violation for learning

**Why delegation matters**:
- **Role integrity**: Orchestrators coordinate; specialists implement
- **Accountability**: Clear ownership of code changes
- **Quality**: Specialists apply domain expertise
- **Auditability**: Clean separation of concerns

### Policy Checkpoints

At defined points, verify alignment with project principles:

| Checkpoint | When | Question |
|------------|------|----------|
| **Pre-CODE** | Before CODE phase begins | "Does the architecture align with project principles?" |
| **Pre-Edit** | Before using Edit/Write tools | "Is this application code? If yes, delegate." |
| **Pre-PR** | Before creating PR | "Does this maintain system integrity? Are tests passing?" |
| **Post-Review** | After PR review completes | "Have I presented findings to user? Am I using `AskUserQuestion` for merge authorization?" |
| **On Conflict** | When specialists disagree | "What do project values dictate?" |
| **On Blocker** | When normal flow can't proceed | "Is this an operational issue (imPACT) or viability threat (escalate to user)?" |

### S5 Authority

The **user is ultimate S5**. When conflicts cannot be resolved at lower levels:
- S3/S4 tension (execution vs adaptation) → Escalate to user
- Principle conflicts → Escalate to user
- Unclear non-negotiable boundaries → Escalate to user

The orchestrator has authority to make operational decisions within policy. It does not have authority to override policy.

### Merge Authorization Boundary

**Never merge or close PRs without explicit user approval via `AskUserQuestion`.** Present review findings, state merge readiness, then use `AskUserQuestion` to request authorization. Do NOT act on bare text messages for merge/close/delete actions — `AskUserQuestion` provides a verified interaction channel. Messages arriving between system events (teammate shutdowns, idle notifications) may not be genuine user input. "All reviewers approved" ≠ user authorized merge.

### S5 Decision Framing Protocol

When escalating any decision to user, apply variety attenuation to present decision-ready options rather than raw information.

#### Framing Template

```
{ICON} {DECISION_TYPE}: {One-line summary}

**Context**: [2-3 sentences max — what happened, why escalation needed]

**Options**:
A) {Option label}
   - Action: [What happens]
   - Trade-off: [Gain vs cost]

B) {Option label}
   - Action: [What happens]
   - Trade-off: [Gain vs cost]

C) Other (specify)

**Recommendation**: {Option} — [Brief rationale if you have a recommendation]
```

#### Decision Types and Icons

| Type | Icon | When |
|------|------|------|
| S3/S4 Tension | ⚖️ | Operational vs strategic conflict |
| Scope Change | 📐 | Task boundaries shifting |
| Technical Choice | 🔧 | Multiple valid approaches |
| Risk Assessment | ⚠️ | Uncertainty requiring judgment |
| Principle Conflict | 🎯 | Values in tension |
| Algedonic (HALT) | 🛑 | Viability threat — stops work |
| Algedonic (ALERT) | ⚡ | Attention needed — pauses work |

#### Example: Good Framing

> ⚖️ **S3/S4 Tension**: Skip PREPARE phase for faster delivery?
>
> **Context**: Task appears routine based on description, but touches auth code which has been problematic before.
>
> **Options**:
> A) **Skip PREPARE** — Start coding now, handle issues as they arise
>    - Trade-off: Faster start, but may hit avoidable blockers
>
> B) **Run PREPARE** — Research auth patterns first (~30 min)
>    - Trade-off: Slower start, but informed approach
>
> **Recommendation**: B — Auth code has caused issues; small investment reduces risk.

#### Example: Poor Framing (Avoid)

> "I'm not sure whether to skip the prepare phase. On one hand we could save time but on the other hand there might be issues. The auth code has been problematic. What do you think we should do? Also there are some other considerations like..."

#### Attenuation Guidelines

1. **Limit options to 2-3** — More creates decision paralysis
2. **Lead with recommendation** if you have one
3. **Quantify when possible** — "~30 min" beats "some time"
4. **State trade-offs explicitly** — Don't hide costs
5. **Keep context brief** — User can ask for more

---

## S4 Checkpoint Protocol

At phase boundaries, the orchestrator performs an S4 checkpoint to assess whether the current approach remains valid.

> **Temporal Horizon**: S4 operates at a **days** horizon—asking questions about the current milestone or sprint, not minute-level implementation details. See the [pact-orchestrator agent body](../agents/pact-orchestrator.md) §S3/S4 Operational Modes for the full horizon model.

### Trigger Points

- After PREPARE phase completes
- After ARCHITECT phase completes
- After CODE phase completes (before TEST)
- When any agent reports unexpected complexity
- On user-initiated "pause and assess"

### Checkpoint Questions

1. **Environment Change**: Has external context shifted?
   - New requirements discovered?
   - Constraints invalidated?
   - Dependencies changed?

2. **Model Divergence**: Does our understanding match reality?
   - Assumptions proven wrong?
   - Estimates significantly off?
   - Risks materialized or emerged?

3. **Plan Viability**: Is current approach still optimal?
   - Should we continue as planned?
   - Adapt the approach?
   - Escalate to user for direction?

4. **Shared Understanding (CT)**: Do we and the completing specialist agree?
   - Orchestrator's understanding matches specialist's handoff?
   - Key decisions interpreted consistently?
   - No misunderstandings disguised as agreement?

   *Verification*: At final gates (TEST→PR, comPACT, plan-mode), `SendMessage` to the completing specialist to confirm your understanding. At intermediate boundaries, the downstream agent's teachback verifies shared understanding. Background: [pact-ct-teachback.md](pact-ct-teachback.md).

### Checkpoint Outcomes

| Finding | Action |
|---------|--------|
| All clear | Continue to next phase |
| Minor drift | Note in handoff, continue |
| Significant change | Pause, assess, may re-run prior phase |
| Fundamental shift | Escalate to user (S5) |

### Checkpoint Format (Brief)

> **S4 Checkpoint** [Phase→Phase]:
> - Environment: [stable / shifted: {what}]
> - Model: [aligned / diverged: {what}]
> - Plan: [viable / adapt: {how} / escalate: {why}]
> - Agreement: [verified / corrected: {what}]

### Output Behavior

**Default**: Silent-unless-issue — checkpoint runs internally; only surfaces to user when drift or issues detected.

**Examples**:

*Silent (all clear)*:
> (Internal) S4 Checkpoint Post-PREPARE: Environment stable, model aligned, plan viable, agreement verified → continue

*Surfaces to user (issue detected)*:
> **S4 Checkpoint** [PREPARE→ARCHITECT]:
> - Environment: Shifted — API v2 deprecated, v3 has breaking changes
> - Model: Diverged — Assumed backwards compatibility, now false
> - Plan: Adapt — Need PREPARE extension to research v3 migration path
> - Agreement: Corrected — Preparer assumed v2 compatibility; confirmed v3 migration needed

### Relationship to Variety Checkpoints

S4 Checkpoints complement Variety Checkpoints (see [Variety Management](pact-variety.md)):
- **Variety Checkpoints**: "Do we have enough response capacity for this complexity?"
- **S4 Checkpoints**: "Is our understanding of the situation still valid?"

Both occur at phase transitions but ask different questions.

---

## S4 Environment Model

S4 checkpoints assess whether our mental model matches reality. The **Environment Model** makes this model explicit—documenting assumptions, constraints, and context that inform decision-making.

### Purpose

- **Make implicit assumptions explicit** — What do we assume about the tech stack, APIs, constraints?
- **Enable divergence detection** — When reality contradicts the model, we notice faster
- **Provide checkpoint reference** — S4 checkpoints compare current state against this baseline

### When to Create

| Trigger | Action |
|---------|--------|
| Start of PREPARE phase | Create initial environment model |
| High-variety tasks (11+) | Required — model complexity demands explicit tracking |
| Medium-variety tasks (7-10) | Recommended — document key assumptions |
| Low-variety tasks (4-6) | Optional — implicit model often sufficient |

### Model Contents

```markdown
# Environment Model: {Feature/Project}

## Tech Stack Assumptions
- Language: {language/version}
- Framework: {framework/version}
- Key dependencies: {list with version expectations}

## External Dependencies
- APIs: {list with version/availability assumptions}
- Services: {list with status assumptions}
- Data sources: {list with schema/format assumptions}

## Constraints
- Performance: {expected loads, latency requirements}
- Security: {compliance requirements, auth constraints}
- Time: {deadlines, phase durations}
- Resources: {team capacity, compute limits}

## Unknowns (Acknowledged Gaps)
- {List areas of uncertainty}
- {Questions that need answers}
- {Risks that need monitoring}

## Invalidation Triggers
- If {assumption X} proves false → {response}
- If {constraint Y} changes → {response}
```

### Location

`docs/preparation/environment-model-{feature}.md`

Created during PREPARE phase, referenced during S4 checkpoints.

### Update Protocol

| Event | Action |
|-------|--------|
| Assumption invalidated | Update model, note in S4 checkpoint |
| New constraint discovered | Add to model, assess impact |
| Unknown resolved | Move from Unknowns to appropriate section |
| Model significantly outdated | Consider returning to PREPARE |

### Relationship to S4 Checkpoints

The Environment Model is the baseline against which S4 checkpoints assess:
- "Environment shifted" → Compare current state to Environment Model
- "Model diverged" → Assumptions in model no longer hold
- "Plan viable" → Constraints in model still valid for current approach

---

## S3/S4 Tension Detection and Resolution

S3 (operational control) and S4 (strategic intelligence) are in constant tension. This is healthy—but unrecognized tension leads to poor decisions.

### Tension Indicators

S3/S4 tension exists when:
- **Schedule vs Quality**: Pressure to skip phases vs need for thorough work
- **Execute vs Investigate**: Urge to code vs need to understand
- **Commit vs Adapt**: Investment in current approach vs signals to change
- **Efficiency vs Safety**: Speed of parallel execution vs coordination overhead

### Detection Phrases

When you find yourself thinking:
- "We're behind, let's skip PREPARE" → S3 pushing
- "Requirements seem unclear, we should dig deeper" → S4 pulling
- "Let's just code it and see" → S3 shortcutting
- "This feels risky, we should plan more" → S4 cautioning

### Resolution Protocol

1. **Name the tension explicitly**:
   > "S3/S4 tension detected: [specific tension]"

2. **Articulate trade-offs**:
   > "S3 path: [action] — gains: [X], risks: [Y]"
   > "S4 path: [action] — gains: [X], risks: [Y]"

3. **Assess against project values**:
   - Does the [pact-orchestrator agent body](../agents/pact-orchestrator.md) favor speed or quality for this project?
   - Is this a high-risk area requiring caution?
   - What has the user expressed preference for?

4. **If resolution is clear**: Decide and document
5. **If resolution is unclear**: Escalate to user (S5)

### Escalation Format

When escalating S3/S4 tension to user, use S5 Decision Framing:

> ⚖️ **S3/S4 Tension**: {One-line summary}
>
> **Context**: [What's happening, why tension exists]
>
> **Option A (S3 — Operational)**: [Action]
> - Gains: [Benefits]
> - Risks: [Costs]
>
> **Option B (S4 — Strategic)**: [Action]
> - Gains: [Benefits]
> - Risks: [Costs]
>
> **Recommendation**: [If you have one, with rationale]

### Integration with S4 Checkpoints

S4 Checkpoints are natural points to assess S3/S4 tension:
- Checkpoint finds drift → S3 wants to continue, S4 wants to adapt → Tension
- Checkpoint finds all-clear but behind schedule → S3 wants to skip phases, S4 wants thoroughness → Tension

When a checkpoint surfaces tension, apply the Resolution Protocol above.

---

## Conversation Theory: Teachback Protocol

> **Source**: Gordon Pask's Conversation Theory, applied to LLM multi-agent systems.
> **Phase**: CT Phase 1 (v3.6.0) — additive, no existing mechanisms changed.

### Core Principle

For LLM agents, **conversation IS cognition**. Understanding doesn't exist inside an agent — it's constructed between agents through conversation. A handoff isn't information transfer; it's one side of a conversation that the receiver must complete.

**Teachback** is the mechanism by which a receiving agent completes that conversation: restating their understanding of upstream work to verify the construction succeeded.

### Vocabulary

| Term | Meaning |
|------|---------|
| **P-individual** | A coherent specialist perspective (agent instance with context). Emphasizes the perspective, not the process. |
| **Conversation continuation** | A handoff that requires the receiver to complete the conversation, not just read it. |
| **Teachback** | Receiver restates understanding to verify construction succeeded. |
| **Agreement level** | Depth of shared understanding: L0 (topic — what), L1 (procedure — how), L2 (purpose — why). |
| **Entailment mesh** | Network of connected concepts where understanding one entails understanding others. |
| **Reasoning chain** | How decisions connect — "X because Y, which required Z." A fragment of the entailment mesh. |

### Teachback Mechanism

When a downstream agent receives an upstream handoff (via `TaskGet`), their first action is to send a teachback message — restating key decisions, constraints, and interfaces before proceeding.

#### Flow

```
1. Agent dispatched with upstream task reference (e.g., "Architect task: #5")
2. Agent reads upstream handoff via `TaskGet(#5)`
3. Agent sends teachback to team-lead via `SendMessage`:
   "[{sender}→team-lead] Teachback: My understanding is... [key decisions restated]. Proceeding unless corrected."
4. Agent proceeds with work (non-blocking)
5. If orchestrator spots misunderstanding, they must `SendMessage` to agent to correct it
```

#### Why Non-Blocking

Blocking teachback (wait for confirmation before working) would serialize everything. Non-blocking gives the orchestrator a window to catch misunderstandings while the agent starts work. Most teachbacks will be correct — we're catching exceptions, not gatekeeping the norm.

#### Teachback Format

```
[{sender}→team-lead] Teachback:
- Building: {what I understand I'm building}
- Key constraints: {constraints I'm working within}
- Interfaces: {interfaces I'll produce or consume}
- Approach: {my intended approach, briefly}
Proceeding unless corrected.
```

Keep teachbacks concise — 3-6 bullet points. The goal is to surface misunderstandings, not to restate the entire handoff.

#### When to Teachback

| Situation | Teachback? |
|-----------|-----------|
| Dispatched for any task | Yes — always restate your understanding of the task before starting |
| Re-dispatched after blocker resolution | Yes — understanding may have shifted |
| Self-claimed follow-up task | Yes — restate understanding of the new task |
| Consultant question (peer asks you something) | No — conversational exchange, not task dispatch |

#### Cost

One extra `SendMessage` per agent dispatch (~100-200 tokens). Cheap insurance against the most dangerous failure mode: **misunderstanding disguised as agreement** — where an agent proceeds with wrong understanding, undetected until TEST phase.

### Agreement Verification (Orchestrator-Side)

Teachback verifies understanding **downstream** (next agent → team-lead). Agreement verification verifies understanding **upstream** (team-lead → previous agent).

#### When to Verify

**Final gates only**: Verify at points where there is no downstream agent whose teachback would catch a misunderstanding. At intermediate phase boundaries (PREPARE→ARCHITECT, ARCHITECT→CODE, CODE→TEST), the downstream agent's teachback provides a safety net — if the orchestrator misreads a handoff, the next agent's teachback will surface the mismatch.

| Gate | Level | Verification Question |
|------|-------|----------------------|
| TEST → PR (orchestrate) | L2 (purpose) | "Does the implementation fulfill the original purpose?" |
| comPACT completion | L1 (procedure) | "Does the deliverable match what was requested?" |
| plan-mode synthesis | L1 (procedure) | "Does my synthesis accurately represent your input?" |

#### Flow

```
1. Specialist completes, delivers handoff
2. Orchestrator reads handoff, forms understanding
3. Orchestrator must `SendMessage` to specialist: "Confirming my understanding: [restates key decisions]. Correct?"
4. Specialist confirms or corrects
5. Orchestrator proceeds with verified understanding (commit, create PR, etc.)
```

For multiple concurrent specialists: send your understanding of all deliverables to each specialist individually. Each specialist confirms their piece.

#### Fallback: Specialist Unavailable

If the specialist has been shut down or is unresponsive when agreement verification is attempted, treat the handoff as accepted and note it in the checkpoint:

> - Agreement: [assumed — specialist unavailable for verification]

### Relationship to Existing Protocols

- **S4 Checkpoints**: Agreement verification extends S4 checkpoints with a CT-informed question. Both run at phase boundaries; S4 asks "is our plan valid?" while CT asks "do we share understanding?"
- **HANDOFF format**: Teachback doesn't change the handoff format. It adds a verification conversation on top of the existing document-based handoff.
- **`SendMessage` prefix convention**: Teachback messages follow the existing `[{sender}→{recipient}]` prefix convention.
- **Conversation Failure Taxonomy**: See [pact-workflows.md](pact-workflows.md) (imPACT section) for diagnosing communication failures between agents.

---

## S2 Coordination Layer

The coordination layer enables parallel agent operation without conflicts. S2 is **proactive** (prevents conflicts) not just **reactive** (resolves conflicts). Apply these protocols whenever multiple agents work concurrently.

### Task System Integration

With PACT Task integration, the `TaskList` serves as a **shared state mechanism** for coordination:

| Use Case | How `TaskList` Helps |
|----------|-------------------|
| **Conflict detection** | Query `TaskList` to see what files/components other agents are working on |
| **Parallel agent visibility** | All in_progress agent Tasks visible via `TaskList` |
| **Convention propagation** | First agent's metadata (decisions, patterns) queryable by later agents |
| **Resource claims** | Agent Tasks can include metadata about claimed resources |

**Coordination via Tasks:**
```
Before parallel dispatch:
1. `TaskList` → check for in_progress agents on same files
2. If conflict detected → sequence or assign boundaries
3. Dispatch agents with Task IDs
4. Monitor via `TaskList` for completion/blockers
```

### Information Flows

S2 manages information flow between agents:

| From | To | Information |
|------|-----|-------------|
| Earlier agent | Later agents | Conventions established, interfaces defined |
| Orchestrator | All agents | Shared context, boundary assignments |
| Any agent | Orchestrator → All others | Resource claims, conflict warnings |
| `TaskList` | All agents | Current in_progress work, blockers, completed decisions |

### Pre-Parallel Coordination Check

Before invoking parallel agents, the orchestrator must:

1. **Identify potential conflicts**:
   - Shared files (merge conflict risk)
   - Shared interfaces (API contract disagreements)
   - Shared state (database schemas, config, environment)

2. **Define boundaries or sequencing**:
   - If conflicts exist, either sequence the work or assign clear file/component boundaries
   - If no conflicts, proceed with parallel invocation
   - **Persist `s2_boundaries`**: `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {"agent_name": ["file_paths"]}})`

3. **Establish resolution authority**:
   - Technical disagreements → Architect arbitrates
   - Style/convention disagreements → First agent's choice becomes standard
   - Resource contention → Orchestrator allocates

### S2 Pre-Parallel Checkpoint Format

When analyzing parallel work, emit proactive coordination signals:

> **S2 Pre-Parallel Check**:
> - Shared files: [none / list with mitigation]
> - Shared interfaces: [none / contract defined by X]
> - Conventions: [pre-defined / first agent establishes]
> - Anticipated conflicts: [none / sequencing X before Y]

**Example**:
> **S2 Pre-Parallel Check**:
> - Shared files: `src/types/api.ts` — Backend defines, Frontend consumes (read-only)
> - Shared interfaces: API contract defined in architecture doc
> - Conventions: Follow existing patterns in `src/utils/`
> - Anticipated conflicts: None

### Conflict Resolution

| Conflict Type | Resolution |
|---------------|------------|
| Same file | Sequence agents OR assign clear section boundaries |
| Interface disagreement | Architect arbitrates; document decision |
| Naming/convention | First agent's choice becomes standard for the batch |
| Resource contention | Orchestrator allocates; others wait or work on different tasks |

### Convention Propagation

When "first agent's choice becomes standard," subsequent agents need to discover those conventions:

1. **Orchestrator responsibility**: When invoking parallel agents after the first completes:
   - Extract key conventions from first agent's output (naming patterns, file structure, API style)
   - Include in subsequent agents' prompts: "Follow conventions established: {list}"

2. **Handoff reference**: Orchestrator passes first agent's key decisions to subsequent agents

3. **For truly parallel invocation** (all start simultaneously):
   - Orchestrator pre-defines conventions in all prompts
   - Or: Run one agent first to establish conventions, then invoke the rest concurrently
   - **Tie-breaker**: If agents complete simultaneously and no first-agent convention exists, use alphabetical domain order (backend, database, frontend) for convention precedence

4. **Persist `established_conventions`**: `TaskUpdate(codePhaseTaskId, metadata={"established_conventions": {"naming": "...", "patterns": "...", "style": "..."}})`

> **State recovery**: After compaction, read the journal's `s2_state_seeded` event for `s2_boundaries` and `established_conventions`; fall back to `TaskGet(codePhaseTaskId).metadata` if unavailable. See [pact-state-recovery.md](pact-state-recovery.md) for the full recovery hierarchy.

### Shared Language

All agents operating in parallel must:
- Use project glossary and established terminology
- Use standardized handoff structure (see [Phase Handoffs](pact-phase-transitions.md#phase-handoffs))

### Parallelization Anti-Patterns

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| **Sequential by default** | Missed parallelization opportunity | Run QDCL; require justification for sequential |
| **Ignoring shared files** | Merge conflicts; wasted work | QDCL catches this; sequence or assign boundaries |
| **Over-parallelization** | Coordination overhead; convention drift | Limit parallel agents; use S2 coordination |
| **Analysis paralysis** | QDCL takes longer than the work | Time-box to 1 minute; default to parallel if unclear |
| **Single agent for batch** | 4 bugs → 1 coder instead of 2-4 coders | **4+ items = multiple agents** (no exceptions) |
| **"Simpler to track" rationalization** | Sounds reasonable, wastes time | Not a valid justification; invoke concurrently anyway |
| **"Related tasks" conflation** | "Related" ≠ "dependent"; false equivalence | Related is NOT blocked; only file/data dependencies block |
| **"One agent can handle it" excuse** | Can ≠ should; missed efficiency | Capability is not justification for sequential |

**Recovery**: If in doubt, default to parallel with S2 coordination active. Conflicts are recoverable; lost time is not.

### Rationalization Detection

When you find yourself thinking these thoughts, STOP—you're rationalizing sequential dispatch:

| Thought | Reality |
|---------|---------|
| "They're small tasks" | Small = cheap to invoke together. Split. |
| "Coordination overhead" | QDCL takes 30 seconds. Split. |

**Valid reasons to sequence** (cite explicitly when choosing sequential):
- "File X is modified by both" → Sequence or define boundaries
- "A's output feeds B's input" → Sequence them
- "Shared interface undefined" → Define interface first, then parallel

### Routine Information Sharing

After each specialist completes work:
1. **Extract** key decisions, conventions, interfaces established
2. **Propagate** to subsequent agents in their prompts
3. **Update** shared context for any agents still running in parallel

This transforms implicit knowledge into explicit coordination, reducing "surprise" conflicts.

### Environment Drift Detection

When dispatching agents during parallel execution, the codebase may have changed since earlier agents were briefed. Use the file tracking system to detect environment drift.

**Before dispatching a new agent (when other agents have already modified files)**:
1. Check the team's `file-edits.json` (maintained by `file_tracker.py`) for files modified since the session started or since the last dispatch
2. If files relevant to the new agent's scope were modified, include an environment delta in the dispatch prompt:
   > "Since your context was set, these files were modified: `src/auth.ts` (by backend-coder), `src/types.ts` (by database-engineer). Review before making assumptions about their current state."
3. This is not a full re-briefing — just a delta awareness signal

**When an agent completes and another agent is still running**:
- Check if the completing agent modified files in the running agent's scope
- If so, send a brief `SendMessage` to the running agent: "Environment changed: {file} was modified by {agent}. Verify your assumptions about it."

**Skip when**: Single-agent execution (no parallel agents = no drift risk).

---

## S1 Autonomy & Recursion

Specialists (S1) have bounded autonomy to adapt within their domain. This section defines those boundaries and enables recursive PACT cycles for complex sub-tasks.

### Autonomy Charter

All specialists have authority to:
- **Adjust implementation approach** based on discoveries during work
- **Request context** from other specialists via the orchestrator
- **Recommend scope changes** when task complexity differs from estimate
- **Apply domain expertise** without micro-management from orchestrator

All specialists must escalate when:
- **Discovery contradicts architecture** — findings invalidate the design
- **Scope change exceeds 20%** — significantly more/less work than expected
- **Security/policy implications emerge** — potential S5 violations discovered
- **Cross-domain dependency** — need changes in another specialist's area

### Self-Coordination

When working in parallel (see [S2 Coordination](pact-s2-coordination.md#s2-coordination-layer)):
- Check S2 protocols before starting if multiple agents are active
- Respect assigned file/component boundaries
- First agent's conventions become standard for the batch
- Report potential conflicts to orchestrator immediately

### Recursive PACT (Nested Cycles)

When a sub-task is complex enough to warrant its own PACT treatment:

**Recognition Indicators:**
- Sub-task spans multiple concerns within your domain
- Sub-task has its own uncertainty requiring research
- Sub-task output feeds multiple downstream consumers
- Sub-task could benefit from its own prepare/architect/code/test cycle

**Protocol:**
1. **Declare**: "Invoking nested PACT for {sub-task}"
2. **Execute**: Run mini-PACT cycle (may skip phases if not needed)
3. **Integrate**: Merge results back to parent task
4. **Report**: Include nested work in handoff to orchestrator

**Constraints:**
- **Nesting limit**: 1 level maximum (prevent infinite recursion)
- **Scope check**: Nested PACT must be within your domain; cross-domain needs escalate to orchestrator
- **Documentation**: Nested cycles report via handoff to parent
- **Algedonic signals**: Algedonic signals from nested cycles still go **directly to user**—they bypass both the nested orchestration AND the parent orchestrator. Viability threats don't wait for hierarchy.

**Example:**
```
Parent task: "Implement user authentication service"
Nested PACT: "Research and implement OAuth2 token refresh mechanism"
  - Mini-Prepare: Research OAuth2 refresh token best practices
  - Mini-Architect: Design token storage and refresh flow
  - Mini-Code: Implement the mechanism
  - Mini-Test: Smoke test the refresh flow
```

### Orchestrator-Initiated Recursion (/PACT:rePACT)

While specialists can invoke nested cycles autonomously, the orchestrator can also initiate them:

| Initiator | Mechanism | When |
|-----------|-----------|------|
| Specialist | Autonomy Charter | Discovers complexity during work |
| Orchestrator | `/PACT:rePACT` command | Identifies complex sub-task upfront |

**Usage:**
- Single-domain: `/PACT:rePACT backend "implement rate limiting"`
- Multi-domain: `/PACT:rePACT "implement audit logging sub-system"`

See [rePACT.md](../commands/rePACT.md) for full command documentation.

---

## Algedonic Signals (Emergency Bypass)

Algedonic signals handle viability-threatening conditions that require immediate user attention. Unlike normal blockers (handled by imPACT), algedonic signals bypass normal orchestration flow.

> **VSM Context**: In Beer's VSM, algedonic signals are "pain/pleasure" signals that bypass management hierarchy to reach policy level (S5) instantly.

For full protocol details, see [algedonic.md](algedonic.md).

### Quick Reference

| Level | Categories | Response |
|-------|------------|----------|
| **HALT** | SECURITY, DATA, ETHICS | All work stops; user must acknowledge |
| **ALERT** | QUALITY, SCOPE, META-BLOCK | Work pauses; user decides |

### Signal Format

```
⚠️ ALGEDONIC [HALT|ALERT]: {Category}

**Issue**: {One-line description}
**Evidence**: {What triggered this}
**Impact**: {Why this threatens viability}
**Recommended Action**: {Suggested response}
```

### Key Rules

- **Any agent** can emit algedonic signals when they recognize trigger conditions
- Orchestrator **MUST** surface signals to user immediately—cannot suppress or delay
- HALT requires user acknowledgment before ANY work resumes
- For **HALT** with parallel agents: send stop individually to each in-progress teammate (see [Lead-Side HALT Fan-Out](algedonic.md#lead-side-halt-fan-out)), preserve work-in-progress, do NOT commit partial work
- ALERT allows user to choose: Investigate / Continue / Stop

### Relationship to imPACT

| Situation | Protocol | Scope |
|-----------|----------|-------|
| Operational blocker | imPACT | "How do we proceed?" |
| Repeated blocker (3+ cycles) | imPACT → ALERT | Escalate to user |
| Viability threat | Algedonic | "Should we proceed at all?" |

---

## Variety Management

Variety = complexity that must be matched with response capacity. Assess task variety before choosing a workflow.

### Task Variety Dimensions

| Dimension | 1 (Low) | 2 (Medium) | 3 (High) | 4 (Extreme) |
|-----------|---------|------------|----------|-------------|
| **Novelty** | Routine (done before) | Familiar (similar to past) | Novel (new territory) | Unprecedented |
| **Scope** | Single concern | Few concerns | Many concerns | Cross-cutting |
| **Uncertainty** | Clear requirements | Mostly clear | Ambiguous | Unknown |
| **Risk** | Low impact if wrong | Medium impact | High impact | Critical |

### Quick Variety Score

Score each dimension 1-4 and sum:

| Score | Variety Level | Recommended Workflow |
|-------|---------------|---------------------|
| **4-6** | Low | `/PACT:comPACT` |
| **7-10** | Medium | `/PACT:orchestrate` |
| **11-14** | High | `/PACT:plan-mode` → `/PACT:orchestrate` |
| **15-16** | Extreme | Research spike → Reassess |

**Calibration Examples**:

| Task | Novelty | Scope | Uncertainty | Risk | Score | Workflow |
|------|---------|-------|-------------|------|-------|----------|
| "Add pagination to existing list endpoint" | 1 | 1 | 1 | 2 | **5** | comPACT |
| "Add new CRUD endpoints following existing patterns" | 1 | 2 | 1 | 2 | **6** | comPACT |
| "Implement OAuth with new identity provider" | 3 | 3 | 3 | 3 | **12** | plan-mode → orchestrate |
| "Build real-time collaboration feature" | 4 | 4 | 3 | 3 | **14** | plan-mode → orchestrate |
| "Rewrite auth system with unfamiliar framework" | 4 | 4 | 4 | 4 | **16** | Research spike → Reassess |

> **Extreme (15-16) means**: Too much variety to absorb safely. The recommended action is a **research spike** (time-boxed exploration to reduce uncertainty) followed by reassessment. After the spike, the task should score lower—if it still scores 15+, decompose further or reconsider feasibility.

### Learning II: Pattern-Adjusted Scoring

Before finalizing the variety score, search pact-memory for recurring patterns in the task's domain. This implements Bateson's Learning II — learning to learn from past experience.

1. **Search**: Query pact-memory for `"{domain} orchestration_calibration OR review_calibration"` and `"{domain} blocker OR stall OR rePACT"`
2. **Assess**: If 5+ memories match a recurring pattern (e.g., "auth tasks consistently underestimated"), bump the relevant variety dimension by 1
3. **Note specialist patterns**: If past calibrations indicate specialist mismatch for this domain, note for specialist selection
4. **Document**: "Variety adjusted from {X} to {Y} due to recurring {pattern}"

**Skip when**: First session on a new project (no calibration data exists yet).

### Variety Strategies

**Attenuate** (reduce incoming variety):
- Apply existing patterns/templates from codebase
- Decompose into smaller, well-scoped sub-tasks
- Constrain to well-understood territory
- Use standards to reduce decision space

**Amplify** (increase response capacity):
- Invoke additional specialists
- Enable parallel execution (primary CODE phase strategy; use QDCL from [orchestrate.md](../commands/orchestrate.md))
- Invoke nested PACT (`/PACT:rePACT`) for complex sub-components
- Run PREPARE phase to build understanding
- Apply risk-tiered testing (CRITICAL/HIGH) for high-risk areas

### Variety Checkpoints

At phase transitions, briefly assess:
- "Has variety increased?" → Consider amplifying (more specialists, nested PACT)
- "Has variety decreased?" → Consider simplifying (skip phases, fewer agents)
- "Are we matched?" → Continue as planned

**Who performs checkpoints**: Orchestrator, at S4 mode transitions (between phases).

### Agent State Model

Derive agent state from progress signals (see agent-teams skill, Progress Signals section) and existing monitoring:

| State | Indicators | Orchestrator Action |
|-------|-----------|-------------------|
| **Converging** | Progress signals show forward movement (files modified, tests passing) | No intervention needed |
| **Exploring** | Progress signals show searching behavior (reading files, no modifications yet) | Normal for early task stages; intervene if persists past ~50% of expected duration |
| **Stuck** | No progress signals for extended period; stall detection triggers | Send context/guidance via SendMessage; escalate to imPACT if unresponsive |

**State transitions**:
- Exploring → Converging: Normal (agent found approach, started implementing)
- Converging → Exploring: Concerning (may indicate blocker or scope expansion)
- Any → Stuck: Intervention needed

**Dependency**: Requires progress signal data from agents. Request progress monitoring in dispatch prompts for tasks where mid-flight visibility matters (variety 7+, parallel execution, novel domains).

### Variety Calibration Record

> **Cybernetic basis**: Bateson's deutero-learning — the system learns to learn by comparing
> predicted difficulty against actual outcomes, creating a feedback loop for scoring accuracy.

At workflow completion (orchestrate wrap-up or comPACT completion), the secretary gathers calibration metrics during HANDOFF processing, asks the team-lead for a brief difficulty assessment, and saves the calibration record to pact-memory. Records feed back into Learning II pattern matching.

**Schema**:

```
CalibrationRecord:
  task_id: str                    # Feature task ID
  domain: str                     # Top-level domain (e.g., "auth", "hooks", "frontend")
  initial_variety_score: int      # Score at orchestration start (4-16)
  actual_difficulty_score: int    # Post-hoc assessment (4-16, same scale)
  dimensions_that_drifted:        # Which dimensions were off
    - dimension: str              # "novelty" | "scope" | "uncertainty" | "risk"
      predicted: int              # 1-4
      actual: int                 # 1-4
  blocker_count: int              # imPACT cycles triggered
  phase_reruns: int               # Phases that had to be redone
  specialist_fit: str | null      # "good" | "undermatched" | "overmatched" | null
  timestamp: str                  # ISO 8601
```

**pact-memory mapping**: Saved via secretary with entities including `orchestration_calibration` AND `{domain}` (required for Learning II queries).

**Post-cycle comparison**: During HANDOFF processing, the secretary:
1. Reads feature task metadata for initial_variety_score
2. Scans TaskList for blocker count and phase rerun count
3. Asks the team-lead for a brief difficulty assessment (higher, lower, or about the same)
4. Computes the full CalibrationRecord and saves to pact-memory
5. If drift exceeds 2 in any dimension, notes as significant for future Learning II queries

---

## The PACT Workflow Family

| Workflow | When to Use | Key Idea |
|----------|-------------|----------|
| **PACT** | Complex/greenfield work | Context-aware multi-agent orchestration |
| **plan-mode** | Before complex work, need alignment | Multi-agent planning consultation, no implementation |
| **comPACT** | Focused, independent tasks | Dispatch concurrent specialists for self-contained tasks. No PACT phases needed. |
| **rePACT** | Complex sub-tasks within orchestration | Recursive nested P→A→C→T cycle (single or multi-domain) |
| **imPACT** | When blocked or need to iterate | Triage: Redo prior phase? Additional agents needed? |
| **pause** | PR open, not ready to merge | Consolidate memory, persist state, shut down teammates |

### Lead-vs-Teammate Completion Responsibilities (per workflow)

| Workflow | Teammate completion authority | Lead completion authority |
|---|---|---|
| `/PACT:orchestrate` | None (write HANDOFF, idle on `awaiting_lead_completion`) | All teammate-owned phase tasks; the feature task |
| `/PACT:comPACT` | None for normal specialists; auditor self-completes signal-tasks | All teammate-owned tasks; the parent comPACT task |
| `/PACT:rePACT` | None (write HANDOFF, idle) | All sub-scope tasks; the parent rePACT task |
| `/PACT:peer-review` | None (reviewer writes review HANDOFF, idles) | All reviewer tasks; the peer-review parent task |
| `/PACT:plan-mode` | None (consultant writes consultation HANDOFF, idles) | All consultant tasks; the plan-mode parent task |
| `/PACT:imPACT` | None (triage agent writes triage HANDOFF, idles) | All triage tasks; the imPACT parent task |

Carve-outs apply across all workflows: signal-tasks (auditor), memory-save (secretary), force-termination (imPACT). See [pact-completion-authority.md](pact-completion-authority.md) for the full acceptance + rejection recipes and carve-out rationale; [Completion Authority](pact-completion-authority.md#completion-authority) holds the slim team-lead-side summary.

---

## plan-mode Protocol

**Purpose**: Multi-agent planning consultation before implementation. Get specialist perspectives synthesized into an actionable plan.

**When to use**:
- Complex features where upfront alignment prevents rework
- Tasks spanning multiple specialist domains
- When you want user approval before implementation begins
- Greenfield work with significant architectural decisions

**Four phases**:

| Phase | What Happens |
|-------|--------------|
| 0. Analyze | Orchestrator assesses scope, selects relevant specialists |
| 1. Consult | Specialists provide planning perspectives in parallel |
| 2. Synthesize | Orchestrator resolves conflicts, sequences work, assesses risk |
| 3. Present | Save plan to `docs/plans/`, present to user, await approval |

**Key rules**:
- **No implementation** — planning consultation only
- **No git branch** — that happens when `/PACT:orchestrate` runs
- Specialists operate in "planning-only mode" (analysis, not action)
- Conflicts surfaced and resolved (or flagged for user decision)

**Output**: `docs/plans/{feature-slug}-plan.md`

**After approval**: User runs `/PACT:orchestrate {task}`, which references the plan.

**When to recommend alternatives**:
- Trivial task → `/PACT:comPACT`
- Unclear requirements → Ask clarifying questions first
- Need research before planning → Run preparation phase alone first

---

## imPACT Protocol

**Trigger when**: Blocked; get similar errors repeatedly; or prior phase output is wrong.

**Diagnostic inputs**: Before triaging, check available signals — progress signal history (if monitoring was requested) reveals whether the agent was converging, exploring, or stuck. Apply the Conversation Failure Taxonomy after choosing an outcome.

**Three questions**:
1. **Redo prior phase?** — Is the issue upstream in P→A→C→T?
2. **Additional agents needed?** — Do we need help beyond the blocked agent's scope/specialty?
3. **Is the agent recoverable?** — Can the blocked agent be resumed or helped, or is it unrecoverable (looping, stalled, context exhausted)?

**Six outcomes**:
| Outcome | When | Action |
|---------|------|--------|
| Redo prior phase | Issue is upstream in P→A→C→T | Re-delegate to relevant agent(s) to redo the prior phase |
| Augment present phase | Need help in current phase | Re-invoke blocked agent with additional context + parallel agents |
| Invoke rePACT | Sub-task needs own P→A→C→T cycle | Use `/PACT:rePACT` for nested cycle |
| Terminate agent | Agent unrecoverable (infinite loop, context exhaustion, stall after resume) | `TaskStop(task_id=taskId)` (force-stop) + `TaskUpdate(taskId, status="completed", metadata={"terminated": true, "reason": "..."})` + fresh spawn with partial handoff |
| Not truly blocked | Neither question is "Yes" | Instruct agent to continue with clarified guidance |
| Escalate to user | 3+ imPACT cycles without resolution | Proto-algedonic signal—systemic issue needs user input |

**Conversation Failure Taxonomy** (diagnostic lens — apply after choosing outcome):

| Type | Symptoms | Recovery |
|------|----------|----------|
| Misunderstanding | Wrong output, no errors | Teachback correction + corrected context |
| Derailment | Loops on same error | Fresh agent, different framing |
| Discontinuity | Lost/stale context | Reconstruct from memory/TaskGet |
| Absence | Insufficient upstream output | Redo prior phase |

---

## comPACT Protocol

**Core idea**: Dispatch concurrent specialists for self-contained tasks. No PACT phases needed. Use orchestrate when phases need to chain — research informing design, design informing code.

comPACT handles tasks that can be decomposed into independent sub-tasks — single-domain or cross-domain — without shared-file dependencies. For independent sub-tasks, it invokes multiple specialists in parallel.

**Available specialists**:
| Shorthand | Specialist | Use For |
|-----------|------------|---------|
| `backend` | pact-backend-coder | Server-side logic, APIs, middleware |
| `frontend` | pact-frontend-coder | UI, React, client-side |
| `database` | pact-database-engineer | Schema, queries, migrations |
| `prepare` | pact-preparer | Research, requirements |
| `test` | pact-test-engineer | Standalone test tasks |
| `architect` | pact-architect | Design guidance, pattern selection |
| `devops` | pact-devops-engineer | CI/CD, Docker, scripts, infrastructure |
| `security` | pact-security-engineer | Security audit of existing code |
| `qa` | pact-qa-engineer | Runtime verification of app behavior |

**Smart specialist selection**:
- *Clear task* → Auto-select (domain keywords, file types, single-domain action)
- *Ambiguous task* → Ask user which specialist

### When to Invoke Multiple Specialists

**MANDATORY: parallel unless tasks share files or have dependencies.** comPACT invokes multiple agents — same type or mixed types — for independent items.

Invoke multiple specialists when:
- Multiple independent items (bugs, components, endpoints)
- No shared files between sub-tasks
- No data or ordering dependencies between sub-tasks

| Task | Agents Invoked |
|------|----------------|
| "Fix 3 backend bugs" | 3 backend-coders (parallel) |
| "Add validation to 5 endpoints" | Multiple backend-coders (parallel) |
| "Update styling on 3 components" | Multiple frontend-coders (parallel) |
| "Add API endpoint + update DB index" | 1 backend-coder + 1 database-engineer (parallel, independent files) |
| "Fix CSS layout + add server logging" | 1 frontend-coder + 1 backend-coder (parallel, no shared files) |

### Pre-Invocation (Required)

1. **Set up worktree** — If already in a worktree for this feature, reuse it. Otherwise, invoke `/PACT:worktree-setup` with the feature branch name. All subsequent work happens in the worktree.
2. **Verify session team exists** — The `{team_name}` team should already exist from session start. If not, create it now: `TeamCreate(team_name="{team_name}")`.
3. **S2 coordination** (if concurrent) — Check for file conflicts, assign boundaries

### S2 Light Coordination (for parallel comPACT)

1. **Check for conflicts** — Do any sub-tasks touch the same files?
2. **Assign boundaries** — If conflicts exist, sequence or define clear boundaries
3. **Set convention authority** — First agent's choices become standard for the batch
4. **Environment drift** — When dispatching subsequent agents after earlier agents complete, check `file-edits.json` for files modified since last dispatch and include relevant deltas in prompts

### Specialist instructions (injected when invoking specialist)

- Work directly from task description
- Check docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist—reference relevant context
- Do not create new documentation artifacts
- Smoke tests only: Verify it compiles, runs, and happy path doesn't crash (no comprehensive unit tests—that's TEST phase work)
- For parallel dispatch or novel domains: include "Send progress signals per the agent-teams skill Progress Signals section" in dispatch prompt

**Escalate to `/PACT:orchestrate` when**:
- Sub-tasks have shared-file dependencies requiring sequenced coordination
- Task requires PREPARE or ARCHITECT phases (significant research or design decisions)
- Specialist reports a blocker (run `/PACT:imPACT` first)

### Auditor Dispatch

An auditor is dispatched alongside coders unless explicitly skipped. To skip, output on its own line so the decision is visible to the user:

> **Auditor skipped**: [justification]

See the [Concurrent Audit Protocol](pact-audit.md) for full details.

**Dispatch is mandatory when**:
- Variety score >= 7 (Medium or higher)
- 3+ coders running in parallel (coordination complexity warrants observation)
- Task touches security-sensitive code (auth, crypto, user input handling)
- Domain has prior history of architecture drift (from pact-memory calibration data)

**Valid skip reasons**: Single coder on familiar pattern, variety reassessed below 7, user requested skip.

### After Specialist Completes

1. **Receive handoff** from specialist(s)
2. **Verify deliverables** — confirm files listed in "Produced" were actually modified (e.g., `git diff --stat`, line counts, grep checks). Never report completion based solely on agent handoff.
3. **Run tests** — verify work passes. If tests fail → return to specialist for fixes before committing.
4. **Create atomic commit(s)** — stage and commit before proceeding
5. **Calibration** — The secretary gathers calibration metrics during HANDOFF processing. When asked, provide a brief difficulty assessment: was actual difficulty higher, lower, or about the same as predicted? Which dimensions surprised you?

**Next steps** — After commit, ask: "Work committed. Create PR?"
- Yes (Recommended) → invoke `/PACT:peer-review`
- Not yet / pause → invoke `/PACT:pause` — consolidates memory, persists state, shuts down teammates. Worktree persists; resume later.
- More work → continue with comPACT or orchestrate

**If blocker reported**:
1. Receive blocker from specialist
2. Run `/PACT:imPACT` to triage
3. May escalate to `/PACT:orchestrate` if task exceeds single-specialist scope

---

## Phase Handoffs

**On completing any phase, state**:
1. What you produced (with file paths)
2. Key decisions made
3. What the next agent needs to know

Keep it brief. No templates required.

---

## Task Hierarchy

This document explains how PACT uses Claude Code's Task system to track work at multiple levels.

### Hierarchy Levels

```
Feature Task (created by orchestrator)
├── Phase Tasks (PREPARE, ARCHITECT, CODE, TEST)
│   ├── Agent Task 1 (specialist work)
│   ├── Agent Task 2 (parallel specialist)
│   └── Agent Task 3 (parallel specialist)
└── Review Task (peer-review phase)
```

### Task Ownership

| Level | Created By | Owned By | Lifecycle |
|-------|------------|----------|-----------|
| Feature | Orchestrator | Orchestrator | Spans entire workflow |
| Phase | Orchestrator | Orchestrator | Active during phase |
| Agent | Orchestrator | Specialist (claim-only); Orchestrator (completion authority) | Specialist claims via `TaskUpdate(status="in_progress")`; orchestrator completes via `TaskUpdate(status="completed")` paired with a wake-signal SendMessage |

Under Agent Teams, specialists claim agent tasks (`pending → in_progress`) and store HANDOFFs in `metadata.handoff`, but the orchestrator transitions agent tasks to `completed` after inspecting the HANDOFF. Two narrow carve-outs (signal-tasks; secretary memory-save) self-complete; see [Completion Authority](pact-completion-authority.md#completion-authority).

### Task States

Tasks progress through: `pending` → `in_progress` → `completed`

- **pending**: Created but not started
- **in_progress**: Active work underway (also covers "done-awaiting-review" — teammate has stored HANDOFF and idles on `awaiting_lead_completion`)
- **completed**: Work finished (success or documented failure); transition is **team-lead-only** on teammate-owned tasks

### Status-by-Actor

| Transition | Actor | Conditions |
|---|---|---|
| `pending → in_progress` (claim) | Teammate | Owns the task; `blockedBy` is empty |
| `in_progress → in_progress` (metadata work) | Teammate | Writes `metadata.handoff` / `metadata.teachback_submit` |
| `in_progress → in_progress` (rejection metadata) | Lead | Writes `metadata.teachback_rejection` / `metadata.handoff_rejection` + sends wake-signal SendMessage |
| `in_progress → completed` | **LEAD ONLY** on teammate-owned tasks | Pairs with wake-signal SendMessage; carve-outs: signal-tasks, secretary memory-save, imPACT force-term |
| `pending → completed` (skip) | Lead | Phase-skip with `metadata.skipped = true` |

### Task A + Task B Dispatch Shape

Every specialist dispatch creates a Task A (teachback) + Task B (primary work) pair. Task B has `blockedBy=[A]`. Lead-completion of Task A auto-unblocks Task B in the task graph; the team-lead pairs the status flip with a wake-signal SendMessage so the idle teammate (on `intentional_wait{reason=awaiting_lead_completion}`) wakes to claim B. The platform does not push a wake on blocker resolution — `blockedBy` is computed at TaskList query time, so the wake-signal SendMessage is required.

### Blocking Relationships

Use `addBlockedBy` to express dependencies:

```
CODE phase task
├── blockedBy: [ARCHITECT task ID]
└── Agent tasks within CODE
    └── blockedBy: [CODE phase task ID]
```

### Metadata Conventions

Agent tasks include metadata for context:

```json
{
  "phase": "CODE",
  "domain": "backend",
  "feature": "user-auth",
  "handoff": {
    "produced": ["src/auth.ts"],
    "uncertainty": ["token refresh edge cases"]
  }
}
```

### Scope-Aware Task Conventions

When decomposition creates sub-scopes, tasks use naming and metadata conventions to maintain scope ownership.

#### Naming Convention

Prefix task subjects with `[scope:{scope_id}]` to make `TaskList` output scannable:

```
[scope:backend-api] ARCHITECT: backend-api
[scope:backend-api] CODE: backend-api
[scope:frontend-ui] CODE: frontend-ui
```

Tasks without a scope prefix belong to the root (parent) orchestrator scope.

#### Scope Metadata

Include `scope_id` in task metadata to enable structured filtering:

```json
{
  "scope_id": "backend-api",
  "phase": "CODE",
  "domain": "backend"
}
```

The parent orchestrator iterates all tasks and filters by `scope_id` metadata to track per-scope progress. Claude Code's Task API does not support native scope filtering, so this convention-based approach is required.

#### Scoped Hierarchy

When decomposition occurs, the hierarchy extends with scope-level tasks:

```
Feature Task (root orchestrator)
├── PREPARE Phase Task (single scope, always)
├── ATOMIZE Phase Task (dispatches sub-scopes)
│   └── Scope Tasks (one per sub-scope)
│       ├── [scope:backend-api] Phase Tasks
│       │   └── [scope:backend-api] Agent Tasks
│       └── [scope:frontend-ui] Phase Tasks
│           └── [scope:frontend-ui] Agent Tasks
├── CONSOLIDATE Phase Task (cross-scope verification)
└── TEST Phase Task (comprehensive feature testing)
```

Scope tasks are created during the ATOMIZE phase. The CONSOLIDATE phase task is blocked by all scope task completions. TEST is blocked by CONSOLIDATE completion.

### Integration with PACT Signals

- **Algedonic signals**: Emit via task metadata or direct escalation
- **Variety signals**: Note in task metadata when complexity differs from estimate
- **Handoff**: Store structured handoff in task metadata on completion

### Example Flow

1. Orchestrator creates Feature task: "Implement user authentication" (parent container)
2. Orchestrator creates PREPARE phase task under the Feature task
3. Orchestrator dispatches pact-preparer with agent task (blocked by PREPARE phase task)
4. Preparer completes, updates task to completed with handoff metadata
5. Orchestrator marks PREPARE complete, creates ARCHITECT phase task
6. Orchestrator creates CODE phase task (blocked by ARCHITECT phase task)
7. Pattern continues through remaining phases

---

## Backend ↔ Database Boundary

**Sequence**: Database delivers schema → Backend implements ORM.

| Database Engineer Owns | Backend Engineer Owns |
|------------------------|----------------------|
| Schema design, DDL | ORM models |
| Migrations | Repository/DAL layer |
| Complex SQL queries | Application queries via ORM |
| Indexes | Connection pooling |

**Collaboration**: If Backend needs a complex query, ask Database. If Database needs to know access patterns, ask Backend.

---

## Test Engagement

| Test Type | Owner |
|-----------|-------|
| Smoke tests | Coders (minimal verification) |
| Unit tests | Test Engineer |
| Integration tests | Test Engineer |
| E2E tests | Test Engineer |

**Coders**: Your work isn't done until smoke tests pass. Smoke tests verify: "Does it compile? Does it run? Does the happy path not crash?" No comprehensive testing—that's TEST phase work.

**Test Engineer**: Engage after Code phase. You own ALL substantive testing: unit tests, integration, E2E, edge cases, adversarial testing. Target 80%+ meaningful coverage of critical paths.

### CODE → TEST Handoff

Coders provide handoff summaries to the orchestrator, who passes them to the test engineer.

**Handoff Format**:
```
1. Produced: Files created/modified
2. Key decisions: Decisions with rationale, assumptions that could be wrong
3. Reasoning chain (optional): How key decisions connect — "X because Y, which required Z"
4. Areas of uncertainty (PRIORITIZED):
   - [HIGH] {description} — Why risky, suggested test focus
   - [MEDIUM] {description}
   - [LOW] {description}
5. Integration points: Other components touched
6. Open questions: Unresolved items
```

Items 1-2 and 4-6 are required. Item 3 (reasoning chain) is recommended — include it unless the task is trivial. Not all priority levels need to be present. Most handoffs have 1-3 uncertainty items total. If you have no uncertainties to flag, explicitly state "No areas of uncertainty flagged" to confirm you considered the question (rather than forgot or omitted it).

**Example**:
```
1. Produced: `src/auth/token-manager.ts`, `src/auth/token-manager.test.ts`
2. Key decisions: Used JWT with 15min expiry (assumed acceptable for this app)
3. Reasoning chain: Chose JWT because stateless auth required; 15min expiry because short-lived tokens reduce replay risk, which required a refresh mechanism
4. Areas of uncertainty:
   - [HIGH] Token refresh race condition — concurrent requests may get stale tokens; test with parallel calls
   - [MEDIUM] Clock skew handling — assumed <5s drift; may fail with larger skew
5. Integration points: Modified `src/middleware/auth.ts` to use new manager
6. Open questions: Should refresh tokens be stored in httpOnly cookies?
```

**Uncertainty Prioritization**:
- **HIGH**: "This could break in production" — Test engineer MUST cover these
- **MEDIUM**: "I'm not 100% confident" — Test engineer should cover these
- **LOW**: "Edge case I thought of" — Test engineer uses discretion

**Test Engineer Response**:
- HIGH uncertainty areas require explicit test cases (mandatory)
- If skipping a flagged area, document the rationale
- Report findings using the Signal Output System (GREEN/YELLOW/RED)

**This is context, not prescription.** The test engineer decides *how* to test, but flagged HIGH uncertainty areas must be addressed.

---

## Cross-Cutting Concerns

Before completing any phase, consider:
- **Security**: Input validation, auth, data protection
- **Performance**: Query efficiency, caching
- **Accessibility**: WCAG, keyboard nav (frontend)
- **Observability**: Logging, error tracking

Not a checklist—just awareness.

---

## Architecture Review (Optional)

For complex features, before Code phase:
- Coders quickly validate architect's design is implementable
- Flag blockers early, not during implementation

Skip for simple features or when "just build it."

---

## Agent Stall Detection

**Stalled indicators** (Agent Teams model):
- TeammateIdle event received but no completion message or blocker was sent via `SendMessage`
- Task status in `TaskList` shows `in_progress` but no `SendMessage` activity from the teammate
- Teammate process terminated without sending a completion message or blocker via `SendMessage`

Detection is event-driven: check at signal monitoring points (after dispatch, on TeammateIdle events, on `SendMessage` receipt). If a teammate goes idle without sending a completion message or blocker, treat as stalled immediately.

**Relationship to agent state model**: Stall detection is the binary endpoint (active vs. stalled). For finer-grained mid-execution assessment (converging/exploring/stuck), see the agent state model in [pact-variety.md](pact-variety.md#agent-state-model). An agent assessed as "stuck" via progress signals may stall if not intervened upon.

### Recovery Protocol

1. Check the teammate's `TaskList` status and any partial task metadata or `SendMessage` output for context on what happened
2. Mark the stalled agent task as `completed` with `metadata={"stalled": true, "reason": "{what happened}"}`
3. Assess: Is the work partially done? Can it be continued from where it stopped?
4. Create a new agent task and spawn a new teammate to retry or continue the work, passing any partial output as context
5. If stall persists after 1 retry, emit an **ALERT** algedonic signal (META-BLOCK category)

### Prevention

Include in agent prompts: "If you encounter an error that prevents completion, send a message via `SendMessage` describing what you completed and store a partial HANDOFF in task metadata rather than silently failing."

### Non-Happy-Path Task Termination

When an agent cannot complete normally (stall, failure, or unresolvable blocker), mark its task as `completed` with descriptive metadata:

Metadata: `{"stalled": true, "reason": "..."}` | `{"failed": true, "reason": "..."}` | `{"blocked": true, "blocker_task": "..."}`

**Convention**: All non-happy-path terminations use `completed` with metadata — no `failed` status exists. This preserves the `pending → in_progress → completed` lifecycle.

---

## Incompleteness Signals

> **Purpose**: Define the signals that indicate a plan section is NOT complete.
> Used by `plan-mode` (producer) to populate the Phase Requirements table,
> and by `orchestrate` (consumer) to verify phase-skip decisions.

A plan section may exist without being complete. Before skipping a phase, the orchestrator checks the corresponding plan section for these 7 incompleteness signals. **Any signal present means the phase should run.**

> **Layer 2**: This protocol serves as Layer 2 of the phase-skip protection system. See [orchestrate.md](../commands/orchestrate.md) "Context Assessment: Phase Skip Decision Flow" for the full 3-layer gate model.

---

### Signal Definitions

| # | Signal | What to Look For | Example |
|---|--------|-------------------|---------|
| 1 | **Unchecked research items** | `- [ ]` checkboxes in "Research Needed" sections | `- [ ] Investigate OAuth2 library options` |
| 2 | **TBD values in decision tables** | Cells containing "TBD" in "Key Decisions" or similar tables | `| Auth strategy | TBD | TBD | Needs research |` |
| 3 | **Forward references** | Deferred work markers using the format `⚠️ Handled during {PHASE_NAME}` | `⚠️ Handled during PREPARE` |
| 4 | **Unchecked questions** | `- [ ]` checkboxes in "Questions to Resolve" sections | `- [ ] Which caching layer to use?` |
| 5 | **Empty or placeholder sections** | Template text still present, or sections with no substantive content | `{Description of architectural approach}` |
| 6 | **Unresolved open questions** | `- [ ]` checkboxes in "Open Questions > Require Further Research" | `- [ ] Performance impact of encryption at rest` |
| 7 | **Research/investigation tasks in implementation plan** | Go/no-go items, feasibility studies, audit tasks, or items explicitly requiring PREPARE-phase runtime execution | `- Investigate whether Redis Streams can replace Kafka for our throughput needs` |

### Detection Guidance

- **Signals 1, 4, 6**: Search for `- [ ]` within the relevant section. Checked items (`- [x]`) are resolved and do not count.
- **Signal 2**: Scan table cells for the literal string "TBD" (case-insensitive).
- **Signal 3**: Search for the exact prefix `⚠️ Handled during`. Informal variants ("deferred to", "will be addressed in") are non-standard but should also raise suspicion.
- **Signal 5**: Look for curly-brace placeholders (`{...}`) or sections containing only headings with no content beneath them.
- **Signal 7**: Scan the implementation plan (e.g., "Implementation Sequence", "Code Phase") for tasks that involve research, investigation, feasibility assessment, or go/no-go decisions. These require PREPARE-phase runtime execution even if the plan's Preparation section appears complete. Common indicators: "investigate", "research", "evaluate", "assess feasibility", "determine whether", "audit", "spike".

### Usage

**In `plan-mode` (Phase 2 synthesis)**: Check each phase's plan section for these signals to populate the Phase Requirements table.

**In `orchestrate` (Context Assessment: Phase Skip Decision Flow)**: The completeness check is Layer 2 of the 3-layer skip protection. Before skipping a phase via an approved plan, verify its plan section passes — all 7 signals absent. Use skip reason `"plan_section_complete"`. (Phases can also be skipped via Layer 3 structured analysis with reason `"structured_gate_passed"` — see [orchestrate.md](../commands/orchestrate.md) for the full decision flow.)

---

## Scope Detection

> **Purpose**: Detect multi-scope tasks during orchestration so the orchestrator can propose
> decomposition before committing to a single-scope execution plan.
> Evaluated after PREPARE phase output is available, before ARCHITECT phase begins.

### Detection Heuristics

The orchestrator evaluates PREPARE output against these heuristic signals to determine whether a task warrants decomposition into sub-scopes.

| Signal | Strength | Description |
|--------|----------|-------------|
| **Distinct domain boundaries** | Strong (2 pts) | Task touches 2+ independent domains, evidenced by separate service boundaries, technology stacks, or specialist areas identified in PREPARE output (e.g., backend API + frontend UI, or changes spanning `services/auth/` and `services/billing/`) |
| **Non-overlapping work areas** | Strong (2 pts) | PREPARE output describes work areas with no shared files or components between them — each area maps to a separate specialist domain |
| **High specialist count** | Supporting (1 pt) | Task would require 4+ specialists across different domains to implement |
| **Prior complexity flags** | Supporting (1 pt) | pact-memory retrieval shows previous multi-scope flags or complexity warnings for this area |

### Counter-Signals

Counter-signals argue against decomposition. Each counter-signal present reduces the detection score by 1 point. Counter-signals **demote confidence** — they do not veto decomposition outright.

| Counter-Signal | Reasoning |
|----------------|-----------|
| **Shared data models across domains** | Sub-scopes would need constant coordination on shared types — single scope is simpler |
| **Small total scope despite multiple domains** | A one-line API change + one-line frontend change does not warrant sub-scope overhead |

### Scoring Model

```
Score = sum(detected heuristic points) - count(counter-signals present)
```

- **Strong** signals contribute **2 points** each
- **Supporting** signals contribute **1 point** each
- **Counter-signals** reduce score by **1 point** each (floor of 0)
- **Decomposition threshold**: Score >= 3

The threshold and point values are tunable. Adjust based on observed false-positive and false-negative rates during canary workflows.

**Single sub-scope guard**: If detection fires but only identifies 1 sub-scope, fall back to single scope. Decomposition with 1 scope adds overhead with no benefit.

### Scoring Examples

| Scenario | Signals | Counter-Signals | Score | Result |
|----------|---------|-----------------|-------|--------|
| Backend + frontend task | Distinct domain boundaries (2) + High specialist count (1) | — | 3 | Threshold met — propose decomposition |
| Backend + frontend + DB migration, no shared models | Distinct domain boundaries (2) + Non-overlapping work areas (2) + High specialist count (1) | — | 5 | All strong signals fire — autonomous tier eligible |
| API change + UI tweak, shared types | Distinct domain boundaries (2) | Small total scope (-1) + Shared data models (-1) | 0 | Below threshold — single scope |

A score of 0 means counter-signals outweighed detection signals, not that no signals were observed. The orchestrator still noted the signals — they were simply insufficient to warrant decomposition.

### Activation Tiers

| Tier | Trigger | Behavior |
|------|---------|----------|
| **Manual** | User invokes `/rePACT` explicitly | Always available — bypasses detection entirely |
| **Confirmed** (default) | Score >= threshold | Orchestrator proposes decomposition via S5 decision framing; user confirms, rejects, or adjusts boundaries |
| **Autonomous** | ALL strong signals fire (Distinct domain boundaries + Non-overlapping work areas) AND no counter-signals AND autonomous mode enabled | Orchestrator auto-decomposes without user confirmation |

**Autonomous mode** is opt-in. Enable by adding to `CLAUDE.md`:

```markdown
autonomous-scope-detection: enabled
```

When autonomous mode is not enabled, all detection-triggered decomposition uses the Confirmed tier.

### Evaluation Timing

1. **PREPARE** phase runs in single scope (always — research output is needed to evaluate signals)
2. If PREPARE was skipped but an approved plan exists, evaluate the plan's Preparation section content against the same heuristics.
3. If neither PREPARE output nor plan content is available:
   - **Variety Scope >= 3** → Force PREPARE to run (high cross-cutting complexity requires research input for reliable detection). Return to step 1.
   - **Variety Scope < 3** → Skip detection entirely (proceed single-scope). Low scope makes multi-scope decomposition unlikely.
4. Orchestrator evaluates PREPARE output (or plan content) against heuristics
5. Score **below threshold** → proceed with single-scope execution (today's default behavior)
6. Score **at or above threshold** → activate the appropriate tier (Confirmed or Autonomous)

### Bypass Rules

- **Ongoing sub-scope execution** does not re-evaluate detection (no recursive detection within sub-scopes). Scoped sub-scopes cannot themselves trigger scope detection -- this bypass rule is the primary architectural mechanism; the 1-level nesting limit (see [S1 Autonomy & Recursion](pact-s1-autonomy.md#s1-autonomy--recursion)) serves as the safety net.
- **comPACT** bypasses scope detection entirely — it dispatches specialists directly without phase chaining
- **Manual `/rePACT`** bypasses detection — user has already decided to decompose

### Evaluation Response

When detection fires (score >= threshold), the orchestrator must present the result to the user using S5 Decision Framing.

#### S5 Confirmation Flow

Use this framing template to propose decomposition:

```
📐 Scope Change: Multi-scope task detected

Context: [What signals fired and why — e.g., "3 distinct domains identified
(backend API, frontend UI, database migration) with no shared files"]

Options:
A) Decompose into sub-scopes: [proposed scope boundaries]
   - Trade-off: Better isolation, parallel execution; overhead of scope coordination

B) Continue as single scope
   - Trade-off: Simpler coordination; risk of context overflow with large task

C) Adjust boundaries (specify)

Recommendation: [A or B with brief rationale]
```

#### User Response Mapping

| Response | Action |
|----------|--------|
| Confirmed (A) | Generate scope contracts (see [pact-scope-contract.md](pact-scope-contract.md)), then proceed to ATOMIZE phase, which dispatches `/PACT:rePACT` for each sub-scope |
| Rejected (B) | Continue single scope (today's behavior) |
| Adjusted (C) | Generate scope contracts with user's modified boundaries, then proceed to ATOMIZE phase, which dispatches `/PACT:rePACT` for each sub-scope |

#### Autonomous Tier

When **all** of the following conditions are true, skip user confirmation and proceed directly to decomposition:

1. ALL strong signals fire (not merely meeting the threshold)
2. NO counter-signals present
3. CLAUDE.md contains `autonomous-scope-detection: enabled`

**Output format**: `Scope detection: Multi-scope (autonomous) — decomposing into [scope list]`

> **Note**: Autonomous mode is opt-in and disabled by default. Users enable it in CLAUDE.md after trusting the heuristics through repeated Confirmed-tier usage.

### Post-Detection: Scope Contract Generation

When decomposition is confirmed (by user or autonomous tier), the orchestrator generates a scope contract for each identified sub-scope before invoking rePACT. See [pact-scope-contract.md](pact-scope-contract.md) for the contract format and generation process.

---

## Scope Contract

> **Purpose**: Define what a sub-scope promises to deliver to its parent orchestrator.
> Scope contracts are generated at decomposition time using PREPARE output and serve as
> the authoritative agreement between parent and sub-scope for deliverables and interfaces.

### Contract Format

Each sub-scope receives a scope contract with the following structure:

```
Scope Contract: {scope-name}

Identity:
  scope_id: {kebab-case identifier, e.g., "backend-api"}
  parent_scope: {parent scope_id or "root"}
  executor: {assigned at dispatch — currently rePACT}

Deliverables:
  - {Expected file paths or patterns this scope produces}
  - {Non-file artifacts: API endpoints, schemas, migrations, etc.}

Interfaces:
  exports:
    - {Types, endpoints, APIs this scope exposes to siblings}
  imports:
    - {What this scope expects from sibling scopes}

Constraints:
  shared_files: []  # Files this scope must NOT modify (owned by siblings)
  conventions: []   # Coding conventions to follow (from parent or prior scopes)
```

### Design Principles

- **Minimal contracts** (~5-10 lines per scope): The consolidate phase catches what the contract does not specify. Over-specifying front-loads context cost into the orchestrator.
- **Backend-agnostic**: The contract defines WHAT a scope delivers, not HOW. The same contract format works whether the executor is rePACT (today) or Agent Teams (future).
- **Generated, not authored**: The orchestrator populates contracts from PREPARE output and detection analysis. Contracts are not hand-written.

### Generation Process

1. Identify sub-scope boundaries from detection analysis (confirmed or adjusted by user)
2. For each sub-scope:
   a. Assign `scope_id` from domain keywords (e.g., "backend-api", "frontend-ui", "database-migration")
   b. List expected deliverables from PREPARE output file references
   c. Identify interface exports/imports by analyzing cross-scope references in PREPARE output
   d. Set shared file constraints by comparing file lists across scopes — when a file appears in multiple scopes' deliverables, assign ownership to one scope (typically the scope with the most significant changes to that file); other scopes list it in `shared_files` (no-modify). The owning scope may modify the file; others must coordinate via the consolidate phase.
   e. Propagate parent conventions (from plan or ARCHITECT output if available)
3. Present contracts in the rePACT invocation prompt for each sub-scope

### Contract Lifecycle

```
Detection fires → User confirms boundaries → Contracts generated
    → Passed to rePACT per sub-scope → Sub-scope executes against contract
    → Sub-scope handoff includes contract fulfillment section
    → Consolidate phase verifies contracts across sub-scopes
```

### Contract Fulfillment in Handoff

When a sub-scope completes, its handoff includes a contract fulfillment section mapping actual outputs to contracted items:

```
Contract Fulfillment:
  Deliverables:
    - ✅ {delivered item} → {actual file/artifact}
    - ❌ {undelivered item} → {reason}
  Interfaces:
    exports: {what was actually exposed}
    imports: {what was actually consumed from siblings}
  Deviations: {any departures from the contract, with rationale}
```

The consolidate phase uses fulfillment sections from all sub-scopes to verify cross-scope compatibility.

### Executor Interface

The executor interface defines the contract between the parent orchestrator and whatever mechanism fulfills a sub-scope. It is the "how" side of the scope contract: while the contract format above defines WHAT a scope delivers, the executor interface defines the input/output shape that any execution backend must implement.

#### Interface Shape

```
Input:
  scope_contract: {read from TaskGet(taskId).metadata.scope_contract}
  worktree_path: {read from TaskGet(taskId).metadata.worktree_path}
  nesting_depth: {read from TaskGet(taskId).metadata.nesting_depth}
  feature_context: {parent feature description, branch, relevant docs}

Output:
  handoff: {standard handoff (6 fields, 5 required) + contract fulfillment section}
  commits: {code committed to branch}
  status: completed  # Non-happy-path uses completed with metadata (e.g., {"stalled": true} or {"blocked": true}) per task lifecycle conventions
```

> **State persistence**: Input fields are stored in per-scope sub-task metadata during ATOMIZE and read via `TaskGet` on entry.

#### Current Executor: rePACT

rePACT implements the executor interface as follows:

| Interface Element | rePACT Implementation |
|-------------------|-----------------------|
| **Input: scope_contract** | Read from `TaskGet(taskId).metadata.scope_contract` on entry (stored by parent during ATOMIZE) |
| **Input: feature_context** | Inherited from parent orchestration context (branch, requirements, architecture) |
| **Input: worktree_path** | Read from `TaskGet(taskId).metadata.worktree_path` on entry (stored by parent during ATOMIZE) |
| **Input: nesting_depth** | Read from `TaskGet(taskId).metadata.nesting_depth` on entry; enforced at 1-level maximum |
| **Output: handoff** | Standard handoff (6 fields, 5 required) with Contract Fulfillment section appended (see [rePACT After Completion](../commands/rePACT.md#after-completion)) |
| **Output: commits** | Code committed directly to the feature branch during Mini-Code phase |
| **Output: status** | Always `completed`; non-happy-path uses metadata (`{"stalled": true, "reason": "..."}` or `{"blocked": true, "blocker_task": "..."}`) per task lifecycle conventions |
| **Delivery mechanism** | Synchronous — agent completes and returns handoff text directly to orchestrator |

See [rePACT.md](../commands/rePACT.md) for the full command documentation, including scope contract reception and contract-aware handoff format.

#### Future Executor: Agent Teams

> **Status**: Agent Teams is experimental, gated behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.
> The API has evolved from earlier community-documented versions (monolithic `TeammateTool` with 13 operations)
> into separate purpose-built tools. The mappings below reflect the current API shape but may change
> before official release. This section is documentation/future reference, not current behavior.

When Claude Code Agent Teams reaches stable release, it could serve as an alternative executor backend. The interface shape remains the same; only the delivery mechanism changes.

| Interface Element | Agent Teams Mapping |
|-------------------|---------------------|
| **Input: scope_contract** | Passed in the teammate spawn prompt via `Task` tool (with `team_name` and `name` parameters) |
| **Input: feature_context** | Inherited via CLAUDE.md (auto-loaded by teammates) plus the spawn prompt |
| **Input: worktree_path** | Worktree working directory (teammate operates in the assigned worktree) |
| **Input: nesting_depth** | Communicated in the spawn prompt; no nested teams allowed (enforced by Agent Teams) |
| **Output: handoff** | `SendMessage` (type: `"message"`) from teammate to team-lead |
| **Output: commits** | Teammate commits directly to the feature branch |
| **Output: status** | `TaskUpdate` via shared task list (`TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet`) |
| **Delivery mechanism** | Asynchronous — teammates operate independently; team-lead receives messages and task updates automatically |

**Key Agent Teams tools**:

| Tool | Purpose | PACT Mapping |
|------|---------|--------------|
| `TeamCreate` | Create a team (with `team_name`, optional `description`) | One team per scoped orchestration |
| `Task` (with `team_name`, `name`) | Spawn a teammate into the team | One teammate per sub-scope |
| `SendMessage` (type: `"message"`) | Direct message from teammate to team-lead | Handoff delivery, blocker reporting |
| `SendMessage` (type: `"shutdown_request"`) | Request teammate graceful exit | Sub-scope completion acknowledgment |
| `TaskCreate`/`TaskUpdate` | Shared task list management | Status tracking across sub-scopes |
| `TeamDelete` | Remove team and task directories | Cleanup after scoped orchestration completes |

**Architectural notes**:

- Teammates load CLAUDE.md, MCP servers, and skills automatically but do **not** inherit the team-lead's conversation history — they receive only the spawn prompt (scope contract + feature context).
- No nested teams are allowed. This parallels PACT's 1-level nesting limit but is enforced architecturally by Agent Teams rather than by convention.
- Agent Teams supports peer-to-peer messaging between teammates (`SendMessage` type: `"message"` with `recipient`), which goes beyond PACT's current hub-and-spoke model. Scoped orchestration would use this for sibling scope coordination during the CONSOLIDATE phase.

#### Design Constraints

- **Backend-agnostic**: The parent orchestrator's logic (contract generation, consolidate phase, failure routing) does not change based on which executor fulfills the scope. Only the dispatch and collection mechanisms differ.
- **Same output shape**: Both rePACT and a future Agent Teams executor produce the same structured output (standard handoff + contract fulfillment). The consolidate phase consumes this output identically regardless of source.
- **Experimental API**: The Agent Teams tool names documented above reflect the current API shape (as of early 2026). Since the feature is experimental and gated, these names may change before stable release. The executor interface abstraction insulates PACT from such changes — only the mapping table needs updating.

---

## Scoped Phases (ATOMIZE and CONSOLIDATE)

> **Purpose**: Define the scoped orchestration phases used when decomposition creates sub-scopes.
> These phases replace the standard ARCHITECT and CODE phases when scope detection fires.
> For single-scope workflows, these phases are skipped entirely.

### ATOMIZE Phase

**Skip criteria**: No decomposition occurred (no scope contracts generated) → Proceed to CONSOLIDATE phase.

This phase dispatches sub-scopes for independent execution. Each sub-scope runs a full PACT cycle (Prepare → Architect → Code → Test) via rePACT.

**Worktree isolation**: Before dispatching sub-scopes, create an isolated worktree for each:
1. Invoke `/PACT:worktree-setup` with suffix branch: `feature-X--{scope_id}`
2. Pass the worktree path to the rePACT invocation so the sub-scope operates in its own filesystem

**Persist scope state**: `TaskUpdate(scopeTaskId, metadata={"scope_contract": {...}, "worktree_path": "/path/to/worktree", "nesting_depth": 1})`

**Dispatch**: Invoke `/PACT:rePACT` for each sub-scope. Sub-scopes read their scope contract from task metadata (not the prompt). Sub-scopes run concurrently (default) unless they share files. When generating scope contracts, ensure `shared_files` constraints are set per the generation process in [pact-scope-contract.md](pact-scope-contract.md) -- sibling scopes must not modify each other's owned files.

**Sub-scope failure policy**: Sub-scope failure is isolated — sibling scopes continue independently. Individual scope failures route through `/PACT:imPACT` to the affected scope only. However, when a sub-scope emits HALT, the parent orchestrator stops ALL sub-scopes (consistent with algedonic protocol: "Stop ALL agents"). Preserve work-in-progress for all scopes. After HALT resolution, review interrupted scopes before resuming.

**Before next phase**:
- [ ] All sub-scope rePACT cycles complete
- [ ] Contract fulfillment sections received from all sub-scopes
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **S4 Checkpoint**: All scopes delivered? Any scope stalled?

---

### CONSOLIDATE Phase

**Skip criteria**: No decomposition occurred → Proceed to TEST phase.

This phase verifies that independently-developed sub-scopes are compatible before comprehensive testing.

**Recover scope state**: Read from `TaskGet(scopeTaskId).metadata` (`scope_contract`, `worktree_path`) for each sub-scope.

**Merge sub-scope branches**: Before running contract verification, merge each sub-scope's work back:
1. For each completed sub-scope, merge its suffix branch to the feature branch
2. Merge: `git merge --no-ff {sub-scope-branch}` — the `--no-ff` preserves scope boundaries in git history
3. On merge conflict → emit algedonic ALERT (cross-scope interference indicates a `shared_files` constraint violation or incomplete contract)
4. Invoke `/PACT:worktree-cleanup` for each sub-scope worktree
5. Proceed to contract verification and integration tests (below) on the merged feature branch

**Delegate in parallel**:
- **`pact-architect`**: Verify cross-scope contract compatibility
  - Compare contract fulfillment sections from all sub-scope handoffs
  - Check that exports from each scope match imports expected by siblings
  - Flag interface mismatches, type conflicts, or undelivered contract items
- **`pact-test-engineer`**: Run cross-scope integration tests
  - Verify cross-scope interfaces work together (API calls, shared types, data flow)
  - Test integration points identified in scope contracts
  - Confirm no shared file constraint violations occurred

**Invoke each with**:
- Feature description and scope contracts
- All sub-scope handoffs (contract fulfillment sections)
- "This is cross-scope integration verification. Focus on compatibility between scopes, not internal scope correctness."

**On consolidation failure**: Route through `/PACT:imPACT` for triage. Possible outcomes:
- Interface mismatch → re-invoke affected scope's coder to fix
- Contract deviation → architect reviews whether deviation is acceptable
- Test failure → test engineer provides details, coder fixes

**Before next phase**:
- [ ] Cross-scope contract compatibility verified
- [ ] Integration tests passing
- [ ] Specialist handoff(s) received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of CONSOLIDATE phase work
- [ ] **S4 Checkpoint**: Scopes compatible? Integration clean? Plan viable?

---

### Related Protocols

- [pact-scope-detection.md](pact-scope-detection.md) — Heuristics for detecting multi-scope tasks
- [pact-scope-contract.md](pact-scope-contract.md) — Contract format and lifecycle
- [rePACT.md](../commands/rePACT.md) — Recursive PACT command for sub-scope execution

---

## Concurrent Audit Protocol

> **Cybernetic basis**: Ashby's Law of Requisite Variety applied to quality assurance —
> a pure post-hoc review (TEST phase) has lower variety than concurrent observation.
> Real-time observation during CODE phase catches architecture drift before it compounds.

The pact-auditor agent provides independent quality observation during the CODE phase, complementing (not replacing) the TEST phase and peer review.

### Dispatch Conditions

The auditor is dispatched alongside coders by default. To skip, the orchestrator outputs on its own line:

> **Auditor skipped**: [justification]

**Dispatch is mandatory when**:
- Variety score >= 7 (Medium or higher)
- 3+ coders running in parallel (coordination complexity warrants observation)
- Task touches security-sensitive code (auth, crypto, user input handling)
- Domain has prior history of architecture drift (from pact-memory calibration data)

**Valid skip reasons**: Single coder on familiar pattern, variety reassessed below 7, user requested skip.

### Hybrid Observation Model

The auditor operates primarily through file observation, not messaging. This minimizes disruption to coders while maintaining quality oversight.

| Method | When | Cost |
|--------|------|------|
| **File reading** (git diff, Read) | Primary — every observation cycle | Zero disruption |
| **TaskList monitoring** | Check coder progress, task status | Zero disruption |
| **SendMessage to coder** | Only when file observation raises a question code alone can't answer | Low disruption (one specific question per message) |
| **RED signal to orchestrator** | Clear architecture violation or requirement misunderstanding | Appropriate disruption |

**Rule of thumb**: 80%+ of observation should be silent file reading. If the auditor is messaging coders frequently, it's disrupting more than observing.

### Observation Phases

**Phase A: Warm-up** (while coders start):
1. Read all available references (architecture doc, plan, dispatch context)
2. Identify key interfaces, high-risk dimensions, cross-cutting requirements
3. Note coder assignments from TaskList
4. Wait for coders to produce initial output before observing

**Phase B: Observation cycles** (periodic):
1. Check modified files: `git diff`, read changed files
2. Compare against reference chain: architecture spec > approved plan > dispatch context
3. Assess concern level and respond:
   - No concern → silent, continue next cycle
   - Minor concern → log internally, observe next cycle (may self-resolve)
   - Significant but ambiguous → SendMessage to coder (one specific question)
   - Clear violation → RED signal to orchestrator immediately

**Phase C: Final observation** (triggered by orchestrator or all coders completing):
1. Sweep all modified files
2. Emit summary signal (GREEN/YELLOW/RED)
3. Store audit summary in task metadata

### Audit Criteria (Priority Order)

1. **Architecture drift** — Module boundaries, interfaces, data flow, dependencies matching the design
2. **Risk-proportional concerns** — High uncertainty areas from variety assessment get extra attention
3. **Cross-agent consistency** — When parallel coders: compatible interfaces? Consistent naming? No semantic overlap?
4. **Cross-cutting gaps** — Error handling patterns, security basics, performance red flags
5. **Requirement alignment** — Solving the right problem as specified?
6. **Decision-log presence (per-PR audit cycle only)** — Verify `docs/decision-logs/{feature}-{domain}.md` exists and is non-empty. Emit YELLOW with the expected path if absent. Advisory only; does not block merge. Not checked during concurrent-with-CODE observation cycles: the log is often legitimately authored in TEST/docs phase, so a per-CODE check produces false-positive advisories. YELLOW (not RED) prevents pressuring teammates to fabricate shallow decision-logs — advisory nudges elicit missing logs; RED on a missing artifact elicits empty ones.

**NOT audited**: Code style, test coverage (TEST phase), code cleanliness mid-work, micro-optimization.

### Structural Verification Discipline

Before emitting GREEN on any **structural acceptance criterion**, the auditor MUST verify the claim against `git diff` ground truth. Pattern-matching on HANDOFF prose, commit messages, or coder self-attestation alone is NOT sufficient evidence. Four internally-consistent layers of prose can all be wrong together; the diff is evidence.

**Rationale**: This instantiates the general rule **`file inspection beats HANDOFF inference`**, established during PR #371 calibration (memory `bcead760`, 2026-04-08) and re-materialized at the auditor layer in PR #501 (memory `bb101a99`, 2026-04-21): "Auditor GREEN signal, coder HANDOFF narrative, and commit message body can all pattern-match to self-attestation without any of them verifying against git diff." HANDOFF narrative is a retrieval aid, not ground truth. The specific failure mode this rule prevents is the PHANTOM-SYMMETRIC-CLAIM variant: in PR #501 commit `bef7f24` (corrected in `7ed354e`), four layers — the coder's HANDOFF prose, the commit message body, the coder's self-attestation messages, and the audit signal — all agreed on a fabricated structural claim (three mirror-added skips at a specific line range) while the actual diff contained one.

**Structural ACs** (diff-verifiable): countable or locatable artifacts — "all files in one commit", "N skips at lines Y–Z", "function `foo` untouched", "helper extracted into a new file", "added after the existing imports block". If the AC contains a count, a line range, a path, or a touched/untouched/added/removed verb, it is structural.

**Non-structural ACs** (judgment calls): "correct function separation", "clean naming", "appropriate error handling", "idiomatic for this codebase". Cannot be derived from diff alone; the auditor should inspect the relevant code and say so in the finding.

**Verification procedure**:

1. Run `git diff <base>..HEAD -- <path>` (or `git show <sha>`) against the path(s) the structural AC references.
2. Count or locate the claimed artifact in the actual diff output — not in the HANDOFF, commit message, or coder messages.
3. If count/location matches the claim → cite the exact diff range in the Evidence field (command + path + hunk header or line range). Specificity requirement: a verifier must be able to reproduce the read.
4. If count/location does NOT match → emit RED (clear violation) or YELLOW (ambiguous) with the discrepancy named. Do NOT emit GREEN. A count mismatch (HANDOFF says N, diff shows M, M ≠ N) is a clear violation — RED. Reserve YELLOW for cases where the count matches but the location or context is partially off.
5. If the AC is not structural → say so in the finding; do NOT manufacture diff citations.

**Failure modes to avoid**:

- **PHANTOM-SYMMETRIC-CLAIM**: HANDOFF prose, commit message, coder self-attestation, and audit signal all agree on a specific structural claim. Agreement across layers is cheap. If you cite "the coder's HANDOFF states…" as evidence for a structural AC, stop and read the diff.
- **VAGUE-DIFF-CITATION**: Evidence field contains "git diff excerpt" or "see diff" with no specific path, hunk, or line range. Not reproducible; indistinguishable from pattern-matching on prose.
- **STRUCTURAL-DRESSING-ON-JUDGMENT-CALL**: GREEN on a judgment-call AC with a fabricated-looking Evidence field. If the AC is a judgment call, name it as such.

### Signal Format

```
📋 AUDIT SIGNAL: [GREEN|YELLOW|RED]

Reference: [architecture doc / plan / dispatch context]
Scope: [which coder(s) / which files]
Finding: [One-line summary]
Evidence: [For structural ACs: `git diff <base>..HEAD -- <path>` plus the specific hunk header or line range that demonstrates the claim. For non-structural ACs: specific file:line of the code inspected. Vague citations like "see diff" are not acceptable — a verifier must be able to reproduce the read.]
Action: [None (GREEN) / Route to test (YELLOW) / Intervene (RED)]
```

### Signal Levels

| Signal | Meaning | When | Orchestrator Response |
|--------|---------|------|---------------------|
| **GREEN** | On track | Final summary; silence during cycles is implicit GREEN | None needed |
| **YELLOW** | Worth noting | Minor drift, convention inconsistency, potential edge case | Pass finding to test engineer as focus area |
| **RED** | Intervene now | Architecture violation, requirement misunderstanding, security concern | SendMessage to affected coder; may pause coder's work |

**Before emitting RED**: Verify via targeted question to the coder when practical. Skip verification for clear-cut violations (e.g., wrong module boundary, missing auth check on sensitive endpoint).

### Reference Fallback Chain

The auditor checks implementation against available references in priority order:

1. **Architecture doc** (`docs/architecture/`) — Most authoritative for design decisions
2. **Approved plan** (`docs/plans/`) — Authoritative for scope and approach
3. **Dispatch context** (task description/metadata) — Authoritative for specific agent instructions
4. **Established conventions** (existing codebase patterns) — When no explicit reference exists

If no reference exists for a concern, the auditor logs it as YELLOW (convention gap) rather than RED (violation).

### Completion Lifecycle

The auditor uses signal-based completion rather than standard HANDOFF:

1. Task is created with `metadata: {"completion_type": "signal"}`
2. Auditor stores final signal as `metadata.audit_summary` via `TaskUpdate`
3. Auditor marks task completed
4. Completion gate accepts `audit_summary` as the completion artifact (the `audit_summary` field in task metadata; no hook validates it; the team-lead verifies presence directly via `TaskGet`).

**audit_summary format**:
```json
{
  "signal": "GREEN",
  "findings": [
    {"level": "YELLOW", "scope": "backend-coder", "finding": "Error handling inconsistent in auth module"}
  ],
  "observation_cycles": 3,
  "files_reviewed": 12
}
```

### Algedonic Escalation

If the auditor discovers a viability threat (not just a quality issue), bypass the signal system and emit a full algedonic signal per [algedonic.md](algedonic.md). Examples:
- Hardcoded credentials discovered in coder's work → HALT SECURITY
- PII being logged → HALT DATA
- Fundamental misunderstanding of requirements → ALERT SCOPE

### Relationship to Other Quality Mechanisms

| Mechanism | Timing | Focus | Scope |
|-----------|--------|-------|-------|
| **Auditor** | During CODE | Architecture drift, requirement alignment | Concurrent observation |
| **TEST phase** | After CODE | Functional correctness, edge cases, coverage | Comprehensive testing |
| **Peer review** | After TEST | Cross-domain quality, code health | Multi-reviewer synthesis |
| **Security review** | During review | Adversarial security analysis | Security-focused |

The auditor is additive — it catches issues during CODE that would otherwise only surface in TEST or review, when the cost of correction is higher.

**Related protocol**: [S4 Checkpoints](pact-s4-checkpoints.md) — Auditor RED signals should prompt an S4 checkpoint to reassess plan viability.

---

## Completion Authority

> **Purpose**: Lead-only completion of teammate-owned tasks. Acceptance is a two-call atomic pair (wake-signal SendMessage FIRST, then status flip); rejection is dual-channel (wake-signal SendMessage FIRST, then metadata write).
>
> **Audience**: PACT team-lead (orchestrator). Teammate-side rules live in [pact-agent-teams §On Completion](../skills/pact-agent-teams/SKILL.md#on-completion--handoff-required) and [pact-agent-teams §On Rejection](../skills/pact-agent-teams/SKILL.md#on-rejection-wake-signal-receipt).

You — the team-lead — are the **only** actor who marks teammate-owned tasks `completed`. Teammates write HANDOFFs to `metadata.handoff`, idle on `intentional_wait{reason=awaiting_lead_completion}`, and wait for your acceptance. The `TaskUpdate(status="completed")` flip is the load-bearing approval action; the paired wake-signal SendMessage is the load-bearing wake.

`blockedBy` is pull-only at the platform level — the platform does NOT push a wake on blocker resolution; `blockedBy` is computed at TaskList query time. Idle teammates cannot self-wake to re-poll, so the wake-signal SendMessage is paired with each metadata or status write that resolves their wait.

### Acceptance — two-call atomic pair (BOTH required, SendMessage FIRST)

1. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Task #<id> accepted. Work complete.", summary="Task accepted")` — wakes the idle teammate so they can claim the next task; writes the wake to the inbox file BEFORE the status flip
2. `TaskUpdate(taskId, status="completed")` — status flip; auto-unblocks any tasks with `blockedBy=[<id>]`

Both calls are **required**. The ordering is load-bearing: `SendMessage` must precede `TaskUpdate` so the lifecycle gate's PostToolUse `_has_paired_sendmessage` scan finds the wake on disk by the time the status flip fires. Reversed ordering produces same-batch races and false-positive `completion_no_paired_send` WARNs even when the pair is structurally correct. Skipping the SendMessage entirely strands the teammate idle on `awaiting_lead_completion` until something else (peer message, your next dispatch) wakes them; `blockedBy` resolution is invisible without the wake.

### Rejection — two-call atomic pair (BOTH required, SendMessage FIRST)

1. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Rejected on Task #<id>. See metadata.{teachback,handoff}_rejection. Revise.", summary="Rejected; revise")` — wakes the teammate so they read the corrections; writes the wake to the inbox file BEFORE the metadata write
2. `TaskUpdate(taskId, metadata={"teachback_rejection": {...}})` (Task A) OR `TaskUpdate(taskId, metadata={"handoff_rejection": {...}})` (Task B) — payload `{reason, corrections, since, revision_number}`

Both calls are **required**, and the ordering matches Acceptance for the same lifecycle-gate reason: SendMessage-first prevents `_has_paired_sendmessage` race conditions on the metadata-write trigger. Skipping the SendMessage leaves the teammate idle on stale `awaiting_lead_completion`, never seeing the corrections — symmetric failure to skipping wake on acceptance. The teammate's `intentional_wait` does not auto-clear when you write rejection metadata; only the wake-signal triggers their CLEAR-and-revise flow. **3+ rejection cycles** on the same task is an imPACT META-BLOCK signal.

**Teammate self-completion carve-outs (predicate-witnessed)** — narrow exemptions where the teammate marks `completed` themselves:

| Carve-out | Trigger | Rule |
|---|---|---|
| Signal-tasks | `metadata.completion_type == "signal"` AND `metadata.type ∈ {"blocker", "algedonic"}` | Auditor + algedonic-emitting agents self-complete; the task IS the signal, no HANDOFF to judge. |
| Memory-save | Owner's team-config `agentType` ∈ `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (currently `{pact-secretary}`) | Secretary self-completes memory-save tasks; team-lead has no acceptance criteria for memory bookkeeping. Resolved via team-config lookup on `member.agentType`, so the carve-out applies regardless of spawn name (`session-secretary`, etc.). |

The canonical predicate `is_self_complete_exempt(task, team_name)` in `shared/intentional_wait.py` witnesses ONLY these two surfaces — pure function for your TaskGet inspection and audit tooling. No hook reads it. Pass `team_name` (read from session context) to get accurate exemption signal for surface 1; surface 2 is independent of `team_name`.

**Related (dispatch surface)**: `member.agentType="pact-secretary"` also gets a dispatch carve-out — no TEACHBACK (single-task dispatch). Third agentType-keyed carve-out, parallel to `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (completion, above) and `WAKE_EXCLUDED_AGENT_TYPES` (wake counting); three frozensets, three behavioral surfaces, fully decoupled. See `agents/pact-orchestrator.md` §11 + `commands/bootstrap.md`.

**Lead-driven force-completion (separate path, not predicate-witnessed)**:

| Path | Trigger | Rule |
|---|---|---|
| imPACT termination | `metadata.terminated == true` | You force-complete an unrecoverable agent's task via `TaskStop` + `TaskUpdate(status="completed", metadata={"terminated": true, "reason": "..."})`. See [imPACT.md](../commands/imPACT.md). The `terminated` marker is recognized directly by audit/inspection; `is_self_complete_exempt` does NOT cover this surface (the team-lead writes status=completed directly). |

**TaskGet metadata-blindness reminder**: `TaskGet` does NOT surface `metadata.handoff`. Read directly:

```
cat ~/.claude/tasks/{team_name}/{taskId}.json | jq .metadata.handoff
```

Inspect the HANDOFF before flipping status. If `metadata.handoff` is missing or empty, do NOT mark the task completed — request the teammate write the HANDOFF first.

---

## Teachback Review

The Task A + Task B dispatch shape gates implementation work behind teachback approval. When dispatching, you create:

- **Task A**: `subject="<role>: TEACHBACK for <feature>"`, owner = teammate. Description states: "Submit TEACHBACK via `metadata.teachback_submit`. SET `intentional_wait{reason=awaiting_lead_completion}`. Do NOT begin Task B."
- **Task B**: `subject="<role>: <primary mission>"`, owner = teammate, `blockedBy=[<Task A id>]`.

Both tasks are created at dispatch time; the teammate receives both in their initial TaskList view, with B greyed out by `blockedBy`.

**Reviewing the TEACHBACK**:

Read `metadata.teachback_submit` directly:

```
cat ~/.claude/tasks/{team_name}/{A_id}.json | jq .metadata.teachback_submit
```

### Read-Trigger Precondition

Before the raw JSON read above is load-bearing, you MUST wait for teammate's wake-signal SendMessage. The 4-point rule:

1. **Wake-signal SendMessage is the load-bearing content-arrival signal.** The teammate's notify SendMessage (sent immediately after their `metadata.teachback_submit` write per [pact-teachback Step 2](../skills/pact-teachback/SKILL.md)) is the only durable signal that the metadata write has landed on disk. Acting on a raw JSON read before that SendMessage arrives risks reading empty or stale metadata mid-write.
2. **Monitor `INBOX_GREW` is an alarm-clock, not a content marker.** When the inbox-watch Monitor fires `INBOX_GREW`, that ends your turn at the next between-tool-call boundary so the platform can deliver the queued SendMessage — but the wake event itself contains no content. See [watch-inbox.md L10+L18](../commands/watch-inbox.md) for the canonical alarm-clock-not-mailbox principle. Do NOT skip ahead to a raw JSON read on `INBOX_GREW`; wait for the SendMessage payload itself to surface in your context.
3. **Raw read MUST follow SendMessage receipt, not precede it.** The ordering is: teammate writes `metadata.teachback_submit` → teammate sends notify SendMessage → Monitor fires `INBOX_GREW` (or platform delivers opportunistically) → your turn opens with the SendMessage in context → THEN you read `cat ~/.claude/tasks/{team_name}/{A_id}.json | jq .metadata.teachback_submit`. Reversing this order produces false-empty reads that have triggered false-positive rejection cycles (see CLAUDE.md pin "Read-after-write race on TaskUpdate metadata writes").
4. **Mitigation for residual race.** If your raw read returns empty `{}` immediately after the wake-signal SendMessage receipt, the metadata write may still be in flight on the platform side. Mitigations (any one suffices): (a) brief 1-2s delay before re-reading; (b) read twice with a short interval and only treat empty as authoritative if both reads agree; (c) trust the SendMessage's GREEN/RED summary as primary and treat the raw read as audit-only. Do NOT reject a teachback or HANDOFF on a single empty raw read.

The symmetric rule applies to HANDOFF inspection (the raw `cat ... | jq .metadata.handoff` read in §Completion Authority above): wait for teammate's wake-signal SendMessage there too before treating the raw read as authoritative.

The same precondition applies symmetrically to the **rejection-receipt path** (see [Rejection Flow](#rejection-flow) below): the teammate must wait for the lead's wake-SendMessage notifying of `metadata.teachback_rejection` or `metadata.handoff_rejection` BEFORE reading the rejection metadata via raw JSON. The asymmetry on either side produces the same read-after-write race class.

Compare against the dispatched task description. Apply the validation discipline from [Validating Incoming Teachbacks](#validating-incoming-teachbacks) — check for both misstatements AND omissions.

**Optional audit step** — write a `teachback_resolution` record before flipping status:

```
TaskUpdate(A_id, metadata={"teachback_resolution": {
    "conditions_met": true,
    "resolution_comment": "<optional one-line rationale>"
}})
```

This write is optional but recommended for audit. It is NOT one of the required calls below.

**Approving the TEACHBACK — two-call atomic pair (BOTH required, SendMessage FIRST)**:

```
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] Teachback accepted on Task #<A_id>. "
        "Task B (#<B_id>) is now claimable."
    ),
    summary="Teachback accepted; Task B claimable"
)
TaskUpdate(A_id, status="completed")
```

The status flip is the load-bearing approval action; the SendMessage is the load-bearing wake. Ordering is load-bearing for the same reason as the top-of-file Acceptance pair — SendMessage-first ensures the lifecycle gate's PostToolUse scan sees the wake on disk before the status flip fires.

**Rejecting the TEACHBACK** — see [Rejection Flow](#rejection-flow) below.

> ⚠️ DO NOT mark Task B `completed` and DO NOT mark Task B `pending`. Task B stays `pending` (its initial state) until the teammate claims it (`status=in_progress`) after wake. Your acceptance affects Task A only; Task B's lifecycle is the teammate's to drive (claim → work → submit HANDOFF → idle for your HANDOFF acceptance).

### Validating Incoming Teachbacks

When an agent sends a TEACHBACK, **compare it against the task as you dispatched it — check for both misstatements AND omissions of the objective, constraints, or success criteria**. If you spot a misunderstanding, reply with a correction via `SendMessage` before any other action — the agent is already working, so the correction window is short. Prevents **misunderstanding disguised as agreement** from going undetected until TEST phase. Once decided, follow the [Acceptance or Rejection two-call atomic pair](#completion-authority).

---

## Rejection Flow

Teachback or HANDOFF inadequate? Reject with **dual-channel delivery** (metadata + SendMessage). Same shape for both rejection types:

**Teachback rejection** (SendMessage FIRST):

```
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] Teachback rejected on Task #<A_id>. "
        "See metadata.teachback_rejection. Revise and re-submit. "
        "Task A remains in_progress."
    ),
    summary="Teachback rejected; revise"
)
TaskUpdate(A_id, metadata={"teachback_rejection": {
    "reason": "<one-line summary>",
    "corrections": ["<correction 1>", "<correction 2>", ...],
    "since": "<canonical_since() output>",
    "revision_number": 1
}})
```

**HANDOFF rejection** (Task B, SendMessage FIRST):

```
SendMessage(
    to="<teammate>",
    message=(
        "[team-lead→<teammate>] HANDOFF rejected on Task #<B_id>. "
        "See metadata.handoff_rejection. Revise."
    ),
    summary="HANDOFF rejected; revise"
)
TaskUpdate(B_id, metadata={"handoff_rejection": {
    "reason": "...",
    "corrections": [...],
    "since": "<canonical_since() output>",
    "revision_number": 1
}})
```

**Why dual-channel**: metadata gives the durable revision spec the teammate reads on wake; SendMessage gives the wake itself. Single-channel via metadata only fails because the idle teammate can't self-wake to read it. Single-channel via SendMessage only loses durability — the corrections need to survive teammate compaction or agent restart.

**Recovery flow on rejection**:

1. Lead writes rejection metadata + sends wake-signal.
2. Teammate wakes, CLEARs `intentional_wait`, reads rejection metadata.
3. Teammate revises (`metadata.teachback_submit` for A, or revises deliverable + `metadata.handoff` for B).
4. Teammate re-SETs `intentional_wait` with fresh `since`, increments `metadata.revision_number`, SendMessage notifies team-lead "revised."
5. Lead reviews; either accepts (per [Completion Authority](#completion-authority)) or rejects again (revision_number = N+1).

> **Cycle limit**: 3+ rejection cycles on the same task is an imPACT META-BLOCK signal. See [imPACT.md](../commands/imPACT.md).

---

## Documentation Locations

| Phase | Output Location |
|-------|-----------------|
| Plan | `docs/plans/` |
| Prepare | `docs/preparation/` |
| Architect | `docs/architecture/` |

**Plan vs. Architecture artifacts**:
- **Plans** (`docs/plans/`): Pre-approval roadmaps created by `/PACT:plan-mode`. Created *before* implementation begins.
- **Architecture** (`docs/architecture/`): Formal specifications created by `pact-architect` *during* the Architect phase.

**No persistent logging for CODE/TEST phases.** Context passes via structured handoffs between agents. Git commits capture the audit trail.

---

## State Recovery Protocol

> **Purpose**: Define how PACT reconstructs workflow state after context compaction,
> session resume, or crash recovery. The session journal is the primary durable store;
> other sources serve as fallbacks.

### Recovery Hierarchy

From most to least durable:

| Source | Location | Survives | Use For |
|--------|----------|----------|---------|
| **Session journal** | `~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl` | Compaction, task GC, TeamDelete, crashes | HANDOFFs, phase progress, variety scores, commits, pause state |
| **Task system** | `TaskList` / `TaskGet` | Compaction (summaries only) | Status, blocking, assignment. Task *files* (metadata) may be GC'd |
| **pact-memory** | `~/.claude/pact-memory/memory.db` | Permanently | Cross-session knowledge (not workflow state) |

### Recovery Triggers

| Trigger | What Runs | Entry Point |
|---------|-----------|-------------|
| **Session start** | Restore previous session context + detect paused work | `session_init.py` → `restore_last_session()`, `check_paused_state()` |
| **Post-compaction** | Orchestrator rebuilds current session state | CLAUDE.md State Recovery steps + workflow command auto-recovery |
| **Manual** | User or orchestrator reads journal directly | CLI: `python3 session_journal.py read --session-dir {session_dir}` |

### Journal Event Types

Events are JSONL entries with common fields `v` (schema version), `type`, and `ts` (UTC).

| Type | Written By | Fields | Recovery Use |
|------|-----------|--------|--------------|
| `session_start` | session_init hook | `team`, `session_id`, `project_dir`, `worktree`, `source` | Session boundary marker; `source` ∈ {`startup`, `resume`, `compact`, `clear`, `unknown`} attributes the event to startup vs auto-compact vs `/clear` vs `/resume` for direct triage (no timing-cluster triangulation needed) |
| `session_end` | session_end hook | `warning` (optional) | Detect incomplete shutdowns |
| `session_paused` | pause command | `pr_number`, `branch`, `worktree_path`, `consolidation_completed`, `team_name` | Resume paused PR work |
| `session_consolidated` | wrap-up, pause commands | `pass`, `task_count`, `memories_saved` (all optional int) | Signal that Pass 2 memory consolidation ran this session — consumed by `check_unpaused_pr` so SessionEnd does not warn on consolidated sessions regardless of PR state |
| `variety_assessed` | orchestrate command | `score`, `dimensions` | Restore variety context |
| `phase_transition` | orchestrate, comPACT | `phase`, `status` (`started`/`completed`) | Determine current phase |
| `checkpoint` | orchestrate command | Workflow-specific snapshot | Fast recovery point |
| `agent_dispatch` | orchestrate, comPACT | `agent`, `task_id`, `domain` | Track active agents |
| `agent_handoff` | agent_handoff_emitter hook | `agent`, `task_subject`, `handoff` (dict) | Completed work (GC-proof HANDOFF store) |
| `commit` | orchestrate, comPACT | `hash`, `message` | Track committed work |
| `s2_state_seeded` | orchestrate command | `boundaries`, `conventions` | Restore S2 coordination state |
| `review_dispatch` | peer-review command | `reviewers`, `pr_number` | Track review phase |
| `review_finding` | peer-review command | `reviewer`, `severity`, `summary` | Aggregate review results |
| `remediation` | peer-review command | `cycle`, `items` | Track fix iterations |
| `pr_ready` | peer-review command | `pr_number`, `status` | Final review state |

### Recovery Steps

**Cross-session recovery** (session resume via `restore_last_session`):

1. Read previous session's journal via `prev_session_dir` extracted from CLAUDE.md (`- Session dir:` line, with fallback derivation from Resume line + project root)
2. Filter `agent_handoff` events → completed work summary
3. Filter `phase_transition` events → phase progress (completed, in-progress)
4. Check `session_end` events → warnings from previous shutdown
5. Truncate long decision summaries to 80 characters
6. Return formatted resume string for orchestrator context

**Paused state detection** (via `check_paused_state`):

1. Read `session_paused` event (most recent) from previous session's journal
2. TTL check: older than 14 days → return stale notice
3. PR validation: `gh pr view` → if MERGED/CLOSED → return informational
4. Return actionable resume prompt with PR number, branch, worktree path

**Post-compaction recovery** (orchestrator rebuilds mid-session):

1. Read session journal for current session → full event history survives
2. `TaskList` → task summaries (status, blocking, ownership)
3. `TaskGet` on in-progress tasks → metadata if task files still exist
4. Journal is authoritative when task metadata is unavailable

### Crash Recovery

The journal survives crashes because:
- **POSIX O_APPEND** guarantees atomic writes — partial writes don't corrupt earlier entries
- **JSONL format** — each line is self-contained; one malformed line doesn't affect others
- **Fail-open reads** — `read_events()` silently skips malformed lines
- **Session-scoped storage** — the journal lives in `~/.claude/pact-sessions/`, not `~/.claude/teams/`, so `TeamDelete` does not remove it

The wrap-up command harvests journal events to pact-memory before session close. The journal persists in the sessions directory for 30 days (TTL cleanup), providing a recovery window even if harvest fails. Paused sessions are exempt from TTL cleanup.

### Content Durability Across Compaction

Claude Code compaction has three durability mechanisms for orchestrator content:

| Mechanism | What Survives | Durability |
|-----------|---------------|------------|
| **Explicit Read calls** | Files loaded via Read tool at bootstrap | **Lossless** — Read tracker auto-re-issues tracked Reads after compaction; `Skills restored` event independently re-processes references above the truncation cut. Two independent restoration paths. |
| **Inline skill body text** | Content written directly in the skill `.md` file | **Partial** — truncated at a cut boundary (~halfway for large files). Late sections silently dropped. |
| **CLAUDE.md / additionalContext** | Routing block, session info, pinned context | **Structural** — re-injected on every turn; highest durability. |



**Verification**: After compaction, all 9 Read targets should appear in `Skills restored` system-reminder events. If any file is missing, the orchestrator still has the SACROSANCT fail-safe summary inline in bootstrap.md.

### Malformed-Stdin Failure Log

When `session_init.py` receives malformed or incomplete stdin (invalid JSON, missing `session_id`, non-string `session_id`, empty/whitespace `session_id`, or an `unknown-*` sentinel), the R3 gate drops the per-session journal anchor to avoid creating an unreapable `unknown-{hex}/` directory. The failure is instead recorded in a global bounded ring buffer at `~/.claude/pact-sessions/_session_init_failures.log` (100-entry cap, JSONL, fail-open). When debugging session start failures that produce no per-session directory — especially failures in teammate sessions whose first-message context is never seen by the user — inspect this log with `cat ~/.claude/pact-sessions/_session_init_failures.log | tail -20`. Each entry records a UTC timestamp, classification (`malformed_json` / `missing_session_id` / `non_string_session_id` / `empty_session_id` / `sentinel_session_id` / `other`), truncated error text (≤200 chars), cwd, and source.

---

## Session Continuity

If work spans sessions, update CLAUDE.md with:
- Current phase and task
- Blockers or open questions
- Next steps

---

## Related

- Agent definitions: `agents/`
- Commands: `commands/`
