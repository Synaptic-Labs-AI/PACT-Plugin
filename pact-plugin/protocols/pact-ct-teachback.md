## Conversation Theory: Teachback Protocol

> **Source**: Gordon Pask's Conversation Theory, applied to LLM multi-agent systems.
> **Phase**: CT Phase 1.5 (additive, L1.5 method-level extension)

### Core Principle

For LLM agents, **conversation IS cognition**. Understanding doesn't exist inside an agent — it's constructed between agents through conversation. A handoff isn't information transfer; it's one side of a conversation that the receiver must complete.

**Teachback** is the mechanism by which a receiving agent completes that conversation: restating their understanding of upstream work to verify the construction succeeded.

### Vocabulary

| Term | Meaning |
|------|---------|
| **P-individual** | A coherent specialist perspective (agent instance with context). Emphasizes the perspective, not the process. |
| **Conversation continuation** | A handoff that requires the receiver to complete the conversation, not just read it. |
| **Teachback** | Receiver restates understanding to verify construction succeeded. |
| **Agreement level** | Depth of shared understanding: L0 (topic — what), L1 (procedure — how), **L1.5 (method — how & why-it-stays-true)**, L2 (purpose — why). |
| **Entailment mesh** | Network of connected concepts where understanding one entails understanding others. |
| **Reasoning chain** | How decisions connect — "X because Y, which required Z." A fragment of the entailment mesh (sender's view). |
| **Method reconstruction** | The receiver's parallel restatement of the upstream's load-bearing decision, the assumptions it depends on, and the contingency if those assumptions change. The L1.5 verification gate — see [Teachback Format](#teachback-format) and [When to Method-Reconstruct](#when-to-method-reconstruct). |

### Teachback Mechanism

When a downstream agent receives an upstream handoff (via `TaskGet`), their first action is to send a teachback message — restating key decisions, constraints, and interfaces before proceeding.

#### Flow

```
1. Agent dispatched as a Task A (TEACHBACK gate) + Task B (primary work, blockedBy=[A]) pair
2. Agent claims Task A, reads the upstream handoff/mission via `TaskGet`
3. Agent writes its teachback to Task A metadata (`metadata.teachback_submit`, 5 canonical
   fields) and sends a wake-signal `SendMessage` to team-lead:
   "[{sender}→team-lead] Teachback submitted on Task #A. Idling on awaiting_lead_completion."
4. Agent SETs `intentional_wait{reason=awaiting_lead_completion}` and idles — it does NOT
   begin Task B (blocking)
5. Team-lead reviews the teachback. On acceptance: wake-`SendMessage` FIRST, then
   `TaskUpdate(A, status="completed")`, which unblocks Task B. On misunderstanding: write
   `metadata.teachback_rejection` + a correction `SendMessage`; the agent revises on Task A.
   The block holds until acceptance.
```

#### Why Blocking

Blocking teachback — the teammate idles on `awaiting_lead_completion` until the lead accepts — catches a misunderstanding BEFORE the teammate burns context on a wrong implementation. The task graph makes this structural: Task B is `blockedBy=[A]`, so work cannot begin until the lead completes the teachback gate. This trades a short serialization delay for elimination of the most dangerous failure mode: misunderstanding disguised as agreement, otherwise undetected until TEST. Enforcement is blocking-by-protocol (the `blockedBy` edge) with advisory runtime hooks.

#### Teachback Format

```
[{sender}→team-lead] Teachback:
- Building: {what I understand I'm building}
- Key constraints: {constraints I'm working within}
- Interfaces: {interfaces I'll produce or consume}
- Approach: {my intended approach, briefly}
- Method reconstruction (when variety ≥ 11):
    - Decision attribution: "I understand {upstream agent} chose {decision} because {their stated reason}"
    - Assumption trace: "This reasoning depends on {assumption A}, {assumption B}, ..."
    - Contingency clause: "If {assumption A or B} changes, the decision should change to {alternative}"
Idling on awaiting_lead_completion until accepted.
```

Keep teachbacks concise — 3-6 bullet points (or 4-7 when method reconstruction is included). The goal is to surface misunderstandings, not to restate the entire handoff. The method-reconstruction bullet is **optional below variety 11** and **required at variety ≥ 11**; see [When to Method-Reconstruct](#when-to-method-reconstruct) for the variety-band gate.

> Note: protocol prose uses capitalized labels (`Decision attribution`, `Assumption trace`, `Contingency clause`); the schema gate keys on the underscore_separated form (`decision_attribution`, `assumption_trace`, `contingency_clause`). Both shapes describe the same triangle — prose labels for human-readable surfaces, JSON keys for mechanical surfaces (SKILL.md, orchestrator.md §12, tests).

#### When to Teachback

| Situation | Teachback? |
|-----------|-----------|
| Dispatched for any task | Yes — always restate your understanding of the task before starting |
| Re-dispatched after blocker resolution | Yes — understanding may have shifted |
| Self-claimed follow-up task | Yes — restate understanding of the new task |
| Consultant question (peer asks you something) | No — conversational exchange, not task dispatch |

#### When to Method-Reconstruct

The L1.5 gate runs against the dispatching task's variety score (see `hooks/shared/variety_scorer.py` — `COMPACT_MAX`, `ORCHESTRATE_MAX`, `PLAN_MODE_MAX`, and `route_workflow`). Constants live at the SSOT; do not hard-code the 6 / 10 / 14 thresholds in skill prose, doc prose, or test code.

| Variety score | Workflow route | Method reconstruction | Lead behavior on absence |
|---|---|---|---|
| 4–6 | `ROUTE_COMPACT` (comPACT) | **Skipped** — not required, not recommended | Accept teachback; absence is the expected default. |
| 7–10 | `ROUTE_ORCHESTRATE` (orchestrate) | **Recommended** — teammate may include; lead MAY ask for it on follow-up | Accept teachback; lead may SendMessage requesting reconstruction on follow-up if upstream decisions are non-trivial. |
| 11–14 | `ROUTE_PLAN_MODE` (plan-mode + orchestrate) | **Required** — teammate MUST include; absence is rejection signal | Reject teachback with `metadata.teachback_rejection{reason="missing_reasoning_reconstruction"}` plus a correction SendMessage. |
| 15–16 | `ROUTE_RESEARCH_SPIKE` (research-spike) | **Required** (treated identically to plan-mode) | Same as plan-mode — reject on absence. |

The lead-side validation gate emits one of 3 rejection reasons in `metadata.teachback_rejection.reason`:

| Reason enum | Trigger | Source gate |
|---|---|---|
| `missing_reasoning_reconstruction` | Required-band teachback omits the field | Presence gate (variety ≥ 11) |
| `malformed_reasoning_reconstruction` | Field present but not a 3-key dict | Schema gate |
| `empty_reasoning_reconstruction_field` | Sub-key missing, non-string, or empty/whitespace | Schema gate |

The `variety_score=None` handling is a **transitional permissiveness**. A future plugin version SHOULD deprecate the None-tolerance and require `variety_score` on every feature task at dispatch time (enforced via `task_lifecycle_gate` or a new dispatch-time predicate). The conservative-now / fail-loud-later trajectory surfaces failures early without breaking pre-existing dispatch state. Until that deprecation lands, `variety_score=None` is treated as the **Recommended band** (equivalent to variety 7–10).

The `TEACHBACK_EXEMPT_AGENT_TYPES` exemption (currently `{pact-secretary}`, defined in `hooks/shared/intentional_wait.py`) covers method reconstruction too — exempt owners bypass the entire teachback gate, including the L1.5 sub-field. No new helper or constant is required.

#### Cost

One extra `SendMessage` per agent dispatch (~100-200 tokens). Cheap insurance against the most dangerous failure mode: **misunderstanding disguised as agreement** — where an agent proceeds with wrong understanding, undetected until TEST phase. When method reconstruction is required (variety ≥ 11), an additional ~50-100 tokens per teachback round-trip.

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
- **Reasoning chain ↔ Method reconstruction**: HANDOFF's `reasoning_chain` (sender's view of their own derivation) and teachback's `reasoning_reconstruction` (receiver's parallel reconstruction) are explicitly symmetric — sender states, receiver reconstructs. Together they give symmetric entailment-mesh discipline across the handoff boundary.
- **Conversation Failure Taxonomy**: See [pact-workflows.md](pact-workflows.md) (imPACT section) for diagnosing communication failures between agents.

---
