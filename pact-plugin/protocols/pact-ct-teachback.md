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

When a downstream agent receives an upstream handoff (via TaskGet), their first action is to send a teachback message — restating key decisions, constraints, and interfaces before proceeding.

#### Flow

```
1. Agent dispatched with upstream task reference (e.g., "Architect task: #5")
2. Agent reads upstream handoff via TaskGet(#5)
3. Agent sends teachback to lead via SendMessage:
   "[{sender}→lead] Teachback: My understanding is... [key decisions restated]. Proceeding unless corrected."
4. Agent proceeds with work (non-blocking)
5. If orchestrator spots misunderstanding, they SendMessage a correction
```

#### Why Non-Blocking

Blocking teachback (wait for confirmation before working) would serialize everything. Non-blocking gives the orchestrator a window to catch misunderstandings while the agent starts work. Most teachbacks will be correct — we're catching exceptions, not gatekeeping the norm.

#### Teachback Format

```
[{sender}→lead] Teachback:
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

One extra SendMessage per agent dispatch (~100-200 tokens). Cheap insurance against the most dangerous failure mode: **misunderstanding disguised as agreement** — where an agent proceeds with wrong understanding, undetected until TEST phase.

### Agreement Verification (Orchestrator-Side)

Teachback verifies understanding **downstream** (next agent → lead). Agreement verification verifies understanding **upstream** (lead → previous agent). Together they cover both sides of each phase boundary.

#### Flow

```
1. Phase specialist completes, delivers handoff
2. Orchestrator reads handoff, forms understanding
3. Orchestrator SendMessages to specialist to verify: "Confirming my understanding: [restates key decisions]. Correct?"
4. Specialist confirms or corrects
5. Orchestrator dispatches next phase with verified understanding
```

#### Agreement Levels by Phase Transition

| Transition | Level | Verification Question |
|-----------|-------|----------------------|
| PREPARE → ARCHITECT | L0 (topic) | "Do we share understanding of WHAT we're building?" |
| ARCHITECT → CODE | L1 (procedure) | "Do we share understanding of HOW we'll build it?" |
| CODE → TEST | L1 (procedure) | "Did the implementation stay coherent with the design?" |
| TEST → PR | L2 (purpose) | "Does the implementation fulfill the original purpose?" |

User involved only if agreement check reveals significant mismatch.

#### Fallback: Specialist Unavailable

If the specialist has been shut down or is unresponsive when agreement verification is attempted, treat the handoff as accepted and note it in the checkpoint:

> - Agreement: [assumed — specialist unavailable for verification]

This avoids blocking phase transitions when a specialist's process has already terminated. The downstream teachback still provides coverage from the receiving side.

### Relationship to Existing Protocols

- **S4 Checkpoints**: Agreement verification extends S4 checkpoints with a CT-informed question. Both run at phase boundaries; S4 asks "is our plan valid?" while CT asks "do we share understanding?"
- **HANDOFF format**: Teachback doesn't change the handoff format. It adds a verification conversation on top of the existing document-based handoff.
- **SendMessage prefix convention**: Teachback messages follow the existing `[{sender}→{recipient}]` prefix convention.

---
