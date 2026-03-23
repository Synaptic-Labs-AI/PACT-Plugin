## Transduction Protocol

> **Cybernetic basis**: Stafford Beer's concept of transduction — information is reconceptualized
> when crossing VSM level boundaries. Distinct from Shannon's source coding (compression efficiency),
> which is addressed by the Channel Capacity protocol.

Transduction defines how information transforms as it crosses boundaries between VSM levels in PACT. Each boundary crossing requires translation because the receiving system operates in a different information domain than the sender.

### Boundary Crossings in PACT

| Crossing | From → To | Translation Required |
|----------|-----------|---------------------|
| S1 → S3 | Agent implementation details → Orchestrator operational status | Compress: specific file changes → summary of decisions and produced artifacts |
| S3 → S4 | Orchestrator execution state → Strategic assessment | Abstract: task progress → phase viability, risk profile, adaptation needs |
| S4 → S5 | Strategic intelligence → Policy-level decisions | Frame: analysis → decision-ready options with trade-offs (S5 Decision Framing) |
| S3 → S1 | Orchestrator dispatch → Agent working context | Expand: task assignment → full context with references, boundaries, guidelines |

### Lossless vs. Lossy Fields

Not all handoff fields carry equal fidelity requirements. Some fields must survive boundary crossings intact; others are intentionally compressed because the receiver operates at a different abstraction level.

**Lossless fields** (must survive intact across boundaries):
- `produced` — File paths and artifacts created/modified (concrete, verifiable)
- `integration_points` — Components touched beyond the agent's primary scope
- `open_questions` — Unresolved items that affect downstream work

**Lossy fields** (intentionally compressed at boundaries):
- `reasoning_chain` — Implementation reasoning is relevant to the coder's peers and test engineer, but the orchestrator only needs the resulting decisions
- `key_decisions` — Full rationale compresses to decision + brief justification at S3 level; further compresses to just the decision at S4 level
- `areas_of_uncertainty` — Priority levels survive; detailed descriptions compress to risk categories

**Why lossy?** Not bandwidth — information domain mismatch. A coder's reasoning about algorithm choice is meaningful to another coder but noise to the orchestrator making phase-transition decisions. The information isn't lost; it's in the task metadata for on-demand retrieval via `TaskGet`.

### Transduction Fidelity Standards

At each boundary crossing, verify that essential information survived translation:

| Standard | Check | Failure Signal |
|----------|-------|----------------|
| **Completeness** | All lossless fields present in handoff | Missing `produced` or `integration_points` in handoff metadata |
| **Accuracy** | Produced files actually exist; integration points are real | File listed in `produced` not found in `git diff`; referenced component doesn't exist |
| **Relevance** | Information matches the receiver's domain | S4 checkpoint receiving implementation-level detail instead of viability assessment |
| **Actionability** | Receiver can act on the information without requesting clarification | Orchestrator needs to `TaskGet` for basic status; agent needs follow-up `SendMessage` for unclear dispatch |

### Handoff as Transduction

The HANDOFF format (see CLAUDE.md "Expected Agent HANDOFF Format") is PACT's primary transduction mechanism. Each field maps to a fidelity category:

| HANDOFF Field | Fidelity | Boundary Behavior |
|---------------|----------|-------------------|
| 1. Produced | Lossless | Passes intact through all boundaries |
| 2. Key decisions | Lossy | Compresses at S3→S4 (rationale drops, decision survives) |
| 3. Reasoning chain | Lossy | Available on-demand via `TaskGet`; not forwarded by default |
| 4. Areas of uncertainty | Mixed | Priority levels lossless; descriptions lossy (compress to categories) |
| 5. Integration points | Lossless | Passes intact; critical for cross-agent coordination |
| 6. Open questions | Lossless | Must survive to reach decision-maker (may be S3 or S5) |

### Transduction Quality Indicators

The orchestrator can assess transduction quality at phase boundaries:

- **High fidelity**: Downstream agents start work without requesting clarification; test engineer's focus matches coder's flagged uncertainties
- **Degraded fidelity**: Agents send `SendMessage` asking for information that should have been in the handoff; test engineer discovers issues not flagged in uncertainty priorities
- **Failed transduction**: Agent works on wrong problem due to missing context; phase produces output misaligned with upstream intent

### Relationship to Other Protocols

- **Channel Capacity** ([pact-channel-capacity.md](pact-channel-capacity.md)): Addresses throughput limits (how much information can cross a boundary per interaction). Transduction addresses translation quality (whether information retains meaning across boundaries).
- **S4 Checkpoints** ([pact-s4-checkpoints.md](pact-s4-checkpoints.md)): Question 4 (Shared Understanding) directly tests transduction fidelity between orchestrator and specialist.
- **Phase Transitions** ([pact-phase-transitions.md](pact-phase-transitions.md)): Handoff format operationalizes transduction at phase boundaries.

---
