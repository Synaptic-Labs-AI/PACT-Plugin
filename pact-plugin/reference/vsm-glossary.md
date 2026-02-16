# VSM Glossary for PACT

> **Purpose**: Define Viable System Model terminology in the context of the PACT framework.
>
> **Usage**: Reference when reading VSM-enhanced PACT documentation or when VSM terms appear in protocols, agents, or commands.
>
> **Created**: 2026-01-10

---

## Core Systems

### S1 — Operations

**VSM Definition**: The primary activities that produce value. The "muscles and organs" of the organization—the parts that do the actual work.

**In PACT**: The specialist agents that execute development tasks:
- `pact-preparer` — Research and requirements gathering
- `pact-architect` — System design and component planning
- `pact-backend-coder` — Server-side implementation
- `pact-frontend-coder` — Client-side implementation
- `pact-database-engineer` — Data layer implementation
- `pact-test-engineer` — Testing and quality assurance
- `pact-n8n` — Workflow automation

**Key Point**: S1 units need autonomy to respond to their local environment while remaining part of the coherent whole.

---

### S2 — Coordination

**VSM Definition**: The coordination function that resolves conflicts between S1 units, establishes shared language, and dampens oscillations. Acts as a communication conduit between S1 and S3.

**In PACT**: Coordination mechanisms including:
- `pact-s2-coordination.md` — Shared coordination protocols
- Decision logs — Standardized documentation
- Pre-parallel conflict detection — Identifying shared resources before parallel execution
- Resolution authority — Who decides when agents disagree

**Key Point**: S2 enables S1 autonomy by handling inter-unit coordination so each unit doesn't need to negotiate directly with every other unit.

---

### S3 — Control (Operations Management)

**VSM Definition**: Manages the ongoing business of S1-S2-S3. Allocates resources, sets performance expectations, optimizes operational efficiency. Focuses on "inside and now."

**In PACT**: The Orchestrator's operational control function:
- Task execution management
- Agent coordination and sequencing
- Resource allocation (which agents, how many)
- Progress tracking
- Blocker resolution (via `imPACT`)

**Key Point**: S3 asks "Are we doing things right?" — focused on efficient execution of the current plan.

---

### S3* — Audit Channel

**VSM Definition**: A sporadic audit channel that allows S3 to directly monitor S1, bypassing S2's filters. The unpredictability prevents S1 from gaming the audit.

**In PACT**: Not currently implemented as a distinct mechanism. The TEST phase provides independent verification (different agent reviews the work), and mandatory uncertainty coverage ensures coder-flagged concerns are tested. However, TEST is sequential and predictable — it lacks the sporadic, unpredictable quality that defines Beer's S3*.

**Future Enhancement**: Real S3*-like behavior could include post-merge spot-checks, rotating reviewer assignments, or random independent code reviews. See the design document for exploration notes.

**Key Point**: Independent verification is valuable regardless of label. PACT's TEST phase achieves separation of concerns between development and testing, even though it doesn't achieve the sporadic audit that S3* represents.

---

### S4 — Intelligence (Development)

**VSM Definition**: Looks at the outside world, scans for threats and opportunities, creates plans for long-term viability. Focuses on "outside and future."

**In PACT**: The intelligence and planning function:
- `plan-mode` — Strategic planning before implementation
- `pact-preparer` — Environment scanning, requirements research
- Adaptation checks — "Should we change course?"
- Risk assessment — "What could go wrong?"

**Key Point**: S4 asks "Are we doing the right things?" — focused on whether the plan still makes sense given external reality.

---

### S5 — Policy (Identity)

**VSM Definition**: Embodies the identity of the organization—values, norms, ethics, culture. The highest decision-making authority. Provides ground rules and enforces them. Balances the tension between S3 and S4.

**In PACT**: The governance layer:
- User as ultimate authority
- CLAUDE.md principles (formalized)
- Non-negotiables (SACROSANCT rules)
- Policy checkpoints
- Arbiter when S3/S4 conflict

**Key Point**: S5 doesn't manage operations—it defines what the system IS and what it will NOT do, regardless of operational pressure.

---

## Key Concepts

### Algedonic Signal

**VSM Definition**: An emergency signal (from Greek *algos* = pain, *hedos* = pleasure) that bypasses the normal management hierarchy. Like pain signals going straight to the brain, these indicate something requiring immediate attention.

**In PACT**: Critical signals that bypass normal orchestration:
- **HALT signals**: Security vulnerabilities, data exposure, ethical violations → immediate stop
- **ALERT signals**: Repeated failures, fundamental misunderstandings → immediate attention

**Contrast with imPACT**: `imPACT` is S3's exception handling (triage within normal flow). Algedonic signals bypass S3 entirely, going direct to S5 (user).

**Key Point**: Not every problem is algedonic. Reserve for viability-threatening situations where normal channels are too slow or might filter the signal.

---

### Autonomy

**VSM Definition**: The capacity of an S1 unit to adapt its behavior based on local conditions without requiring permission from higher systems for every action.

**In PACT**: Specialist agents' authority to:
- Adjust implementation approach based on discoveries
- Request additional context from other specialists
- Recommend scope changes
- Invoke nested PACT cycles for complex sub-tasks

**Bounded by**: Escalation requirements (contradicts architecture, exceeds scope, security implications)

**Key Point**: Autonomy isn't independence—it's the freedom to adapt within defined boundaries while remaining part of the coherent whole.

---

### Cybernetic Isomorphism

See: **Recursion**

---

### Recursion (Cybernetic Isomorphism)

**VSM Definition**: Viable systems contain viable systems. Each S1 unit can be modeled using the identical VSM structure. A department is a viable system within a company, which is a viable system within an industry.

**In PACT**: Nested PACT cycles:
- A complex feature can contain sub-features, each with own P→A→C→T cycle
- A specialist can invoke nested PACT for complex sub-tasks
- The same structure applies at every level

**Nesting Limit**: 1 level in PACT (to prevent infinite recursion)

**Key Point**: Recursion means the same principles and structure apply at every scale—you don't need different management theories for different levels.

---

### Requisite Variety (Ashby's Law)

**VSM Definition**: "Only variety can absorb variety." A controller must have at least as much variety (range of possible responses) as the system it's trying to control. If the environment has 100 possible states, the controller needs at least 100 possible responses.

**In PACT**: Variety management:
- **Task variety**: Novelty, scope, uncertainty, risk
- **Response capacity**: Available specialists, parallelization, tools, precedent
- **Attenuators**: Reduce incoming complexity (standards, templates, decomposition)
- **Amplifiers**: Increase response capacity (more agents, nested cycles, research)

**Key Point**: You can't control a complex situation with a simple response. Either simplify the situation (attenuate) or increase your response capacity (amplify).

---

### Variety

**VSM Definition**: The number of possible states a system can be in. A measure of complexity. High variety = many possible states = more complex.

**In PACT**: Task complexity dimensions:
- **Novelty**: Routine → Familiar → Novel → Unprecedented
- **Scope**: Single concern → Cross-cutting
- **Uncertainty**: Clear → Ambiguous → Unknown
- **Risk**: Low → Critical

**Key Point**: Variety isn't good or bad—it's a measure that helps you match your response appropriately.

---

### Variety Amplifier

**VSM Definition**: Something that increases the variety (response capacity) of a controller. Allows the system to handle more complex situations.

**In PACT Examples**:
- Invoking additional specialists
- Enabling parallel execution
- Running nested PACT cycles
- Research phase to build understanding
- AI-assisted tools

---

### Variety Attenuator

**VSM Definition**: Something that reduces the variety (complexity) coming into a system. Simplifies what the controller has to deal with.

**In PACT Examples**:
- Applying existing patterns/templates
- Decomposing into smaller sub-tasks
- Constraining scope to well-understood territory
- Using standards to reduce decision space
- Bounded contexts

---

### Viability

**VSM Definition**: The capacity of a system to maintain a separate existence—to survive and thrive in a changing environment. Not just current survival, but ongoing adaptability.

**In PACT**: A development workflow that can:
- Complete current tasks successfully
- Adapt when requirements change
- Handle unexpected complexity
- Recover from failures
- Maintain quality under pressure
- Evolve over time

**Key Point**: Viability isn't efficiency—a highly efficient but brittle system isn't viable. Viability includes resilience and adaptability.

---

## Operational Terms

These terms are specific to PACT's implementation of VSM concepts.

### Environment Model

**Definition**: An explicit documentation of the assumptions, constraints, and context that inform S4's assessment of plan validity. Created during PREPARE phase, referenced during S4 checkpoints.

**Location**: `docs/preparation/environment-model-{feature}.md`

**Contents**:
- Tech stack assumptions (language, framework, dependencies)
- External dependencies (APIs, services, data sources)
- Constraints (performance, security, time, resources)
- Unknowns (acknowledged gaps, questions needing answers)
- Invalidation triggers (what would force approach changes)

**When Required**:
- Variety 11+: Required (high complexity demands explicit tracking)
- Variety 7-10: Recommended (document key assumptions)
- Variety 4-6: Optional (implicit model often sufficient)

**Key Point**: The Environment Model makes implicit assumptions explicit. S4 checkpoints compare current reality against this baseline to detect divergence.

**Status**: The formal environment model artifact (`docs/preparation/environment-model-*.md`) has been retired. Key assumptions are now documented directly in HANDOFF output and checked at S4 checkpoints. The concept remains valid — the delivery mechanism changed.

---

### Decision Log

**Definition**: Standardized documentation produced during the CODE phase that captures implementation decisions, rationale, and context for subsequent phases.

**Location**: `docs/decision-logs/{feature}-{domain}.md`

**Contents**:
- What was implemented and why
- Key decisions and trade-offs
- Assumptions made
- Known limitations
- Areas of uncertainty (where bugs might hide)

**Key Point**: Decision logs explain the "why" not the "what"—code shows what was done, the log explains the reasoning.

---

### Orchestration Decision Log

**Definition**: S3-level audit trail maintained by the orchestrator during `/PACT:orchestrate` runs. Captures orchestration decisions (variety assessment, agent selection, phase outcomes, S4 checkpoints) for retrospective analysis and pattern recognition.

**Location**: `docs/decision-logs/orchestration-{feature}.md`

**Distinction from Decision Log**:
- **Decision Log**: Created by *agents* during CODE phase, documenting *implementation* decisions
- **Orchestration Decision Log**: Created by *orchestrator*, documenting *orchestration* decisions (what agents to invoke, how to coordinate, when to adapt)

**Format Tiers**:
- Variety 7-9: Lightweight (key decisions only)
- Variety 10+: Full format (complete audit trail with S3/S4 tensions, algedonic signals, retrospective)
- Variety 4-6: None (comPACT territory—too simple for orchestration log)

**Key Point**: The orchestration log provides meta-level visibility into how the orchestration was conducted, separate from what was implemented.

---

### META-BLOCK

**Definition**: An ALERT-level algedonic signal category triggered when 3+ consecutive imPACT cycles fail to resolve the same blocker.

**Triggers**: Same blocker recurring, systemic issue detected, unable to make progress despite multiple attempts.

**Response**: User attention required—may indicate fundamental misunderstanding or need to restart from an earlier phase.

**Key Point**: META-BLOCK is proto-algedonic—it starts as operational (imPACT) but escalates to viability concern (ALERT) through repetition.

---

### Override Protocol

**Definition**: The procedure for continuing work after a HALT signal when the user explicitly chooses to proceed despite identified risks.

**Requirements**:
1. Acknowledge the **specific risk** (not just "I understand")
2. Explain **why** proceeding is acceptable
3. Accept **responsibility** for consequences

**Documentation**: Logged in session notes and decision log with "⚠️ Overrode {category} HALT: {justification}"

**Key Point**: Overrides don't carry forward—if the risk materializes later, a new HALT is required.

---

### S3/S4 Tension

**Definition**: The inherent conflict between S3 (operational control: "execute now") and S4 (strategic intelligence: "are we doing the right thing?"). This tension is natural and healthy, but unrecognized tension leads to poor decisions.

**Common Manifestations**:
- Schedule vs Quality (skip phases vs thorough work)
- Execute vs Investigate (code now vs understand first)
- Commit vs Adapt (stay course vs change approach)
- Efficiency vs Safety (speed vs coordination overhead)

**In PACT**: When tension is detected:
1. Name it explicitly
2. Articulate trade-offs for each path
3. Resolve based on project values or escalate to user (S5)

**Key Point**: S5 is the arbiter of S3/S4 tension when values alone don't resolve it. The user decides which trade-off is acceptable.

---

### Research Spike

**Definition**: A time-boxed exploration activity recommended for extreme variety tasks (score 15-16) to reduce uncertainty before committing to implementation.

**Purpose**: Reduce task variety by building understanding, testing assumptions, and mapping unknowns.

**Outcome**: After the spike, reassess—the task should now score lower. If still 15+, decompose further or reconsider feasibility.

**Key Point**: A spike is not implementation—it's reconnaissance. The goal is reducing variety, not producing code.

---

### Decision Scope

**Definition**: The characteristic scope at which each VSM system operates. Different systems naturally focus on different levels of decision-making.

**In PACT**:

| System | Scope | Focus |
|--------|-------|-------|
| **S1** | Current subtask | Agent executing specific implementation |
| **S3** | Current task/phase | Orchestrator coordinating current feature |
| **S4** | Current milestone | Planning, adaptation, risk assessment |
| **S5** | Project identity | Values, principles, non-negotiables |

**Key Point**: When making decisions, consider which scope applies. If you're in S3 mode worrying about next milestone's features, that's an S4-scope question — note it for later.

---

### Variety Assessment

**Definition**: A numeric assessment of task complexity (4-16) used to select the appropriate workflow. Score four dimensions 1-4 each and sum.

**Dimensions** (each scored 1-4):
| Dimension | 1 (Low) | 4 (Extreme) |
|-----------|---------|-------------|
| Novelty | Routine | Unprecedented |
| Scope | Single concern | Cross-cutting |
| Uncertainty | Clear | Unknown |
| Risk | Low impact | Critical |

**Thresholds**:
| Score | Workflow |
|-------|----------|
| 4-6 | comPACT |
| 7-10 | orchestrate |
| 11-14 | plan-mode → orchestrate |
| 15-16 | Research spike → Reassess |

**Key Point**: The scoring forces explicit evaluation of all four dimensions, preventing snap judgments based on a single dimension.

---

## Quick Reference Table

| Term | One-Line Definition | PACT Equivalent |
|------|--------------------| ----------------|
| S1 | Primary operations | Specialist agents |
| S2 | Coordination between S1 units | Protocols, conflict resolution |
| S3 | Operational control (inside-now) | Orchestrator execution mode |
| S3* | Audit channel bypassing S2 | Not implemented; TEST provides independent verification (see entry) |
| S4 | Intelligence (outside-future) | plan-mode, adaptation checks |
| S5 | Policy/identity/values | User + CLAUDE.md principles |
| Algedonic | Emergency bypass signal | HALT/ALERT to user |
| Autonomy | Local adaptation authority | Agent autonomy charter |
| S3/S4 Tension | Operational vs strategic conflict | Name, trade-off, resolve/escalate |
| Recursion | Viable systems within viable systems | Nested PACT cycles |
| Requisite Variety | Controller needs matching complexity | Variety budget assessment |
| Variety | Measure of complexity/possible states | Task complexity dimensions |
| Viability | Capacity for ongoing existence | Adaptive, resilient workflow |
| Environment Model | Explicit assumptions and constraints | Captured in HANDOFF output (artifact retired) |
| Decision Log | Implementation documentation | `docs/decision-logs/` |
| Orchestration Decision Log | S3-level orchestration audit trail | `docs/decision-logs/orchestration-*` |
| META-BLOCK | 3+ imPACT cycles → ALERT | Escalation to user |
| Override Protocol | HALT continuation procedure | Justified risk acceptance |
| Research Spike | Extreme variety exploration | Pre-implementation recon |
| Decision Scope | Scope level for each VSM system | S1=subtask, S3=task, S4=milestone, S5=project |
| Variety Assessment | 4-16 complexity score (4 dimensions) | Workflow ceremony selector |

---

## Further Reading

- Beer, Stafford. *Diagnosing the System for Organizations* (1985) — Most accessible introduction
- Beer, Stafford. *Brain of the Firm* (1972) — Original VSM formulation
- Beer, Stafford. *The Heart of Enterprise* (1979) — Detailed theoretical treatment
- [Metaphorum VSM Resources](https://metaphorum.org/staffords-work/viable-system-model)
