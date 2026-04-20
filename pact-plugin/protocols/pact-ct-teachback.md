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
3. Agent sends teachback to lead via `SendMessage`:
   "[{sender}→lead] Teachback: My understanding is... [key decisions restated]. Proceeding unless corrected."
4. Agent proceeds with work (non-blocking)
5. If orchestrator spots misunderstanding, they must `SendMessage` to agent to correct it
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

One extra `SendMessage` per agent dispatch (~100-200 tokens). Cheap insurance against the most dangerous failure mode: **misunderstanding disguised as agreement** — where an agent proceeds with wrong understanding, undetected until TEST phase.

### Agreement Verification (Orchestrator-Side)

Teachback verifies understanding **downstream** (next agent → lead). Agreement verification verifies understanding **upstream** (lead → previous agent).

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

For multiple concurrent specialists: broadcast your understanding of all deliverables. Each specialist confirms their piece.

#### Fallback: Specialist Unavailable

If the specialist has been shut down or is unresponsive when agreement verification is attempted, treat the handoff as accepted and note it in the checkpoint:

> - Agreement: [assumed — specialist unavailable for verification]

### Relationship to Existing Protocols

- **S4 Checkpoints**: Agreement verification extends S4 checkpoints with a CT-informed question. Both run at phase boundaries; S4 asks "is our plan valid?" while CT asks "do we share understanding?"
- **HANDOFF format**: Teachback doesn't change the handoff format. It adds a verification conversation on top of the existing document-based handoff.
- **`SendMessage` prefix convention**: Teachback messages follow the existing `[{sender}→{recipient}]` prefix convention.
- **Conversation Failure Taxonomy**: See [pact-workflows.md](pact-workflows.md) (imPACT section) for diagnosing communication failures between agents.

---

## Honest Reframe: Ritual Enforcement, Not Adversarial Defense

> Added for issue #401 (teachback gate). Source: plan-mode security-engineer
> finding, validated empirically 2026-04-17 in session 9097e100.

The teachback gate (`teachback_gate.py` + `task_schema_validator.py`) provides
**hard-to-accidentally-bypass ritual enforcement for honest-but-careless LLM
output — NOT adversarial defense.**

What the gate closes:
- Teammate forgets to send the teachback SendMessage → gate blocks
  Edit/Write/Agent/NotebookEdit until `teachback_submit` metadata is written.
- Orchestrator writes "Pre-review teachback OPTIONAL" in a dispatch prompt
  (observed in session 9097e100, PR #447 round 2) → gate binds on
  `metadata.teachback_submit` presence + schema validity, NOT on task-description
  wording. The "OPTIONAL" prose becomes structurally inert.
- Teammate submits a placeholder teachback ("I understand the task and will
  proceed") → gate's content-shape validators (citation regex,
  substring-inequality, template-phrase blocklist) reject the placeholder.

What the gate explicitly does NOT close (accepted risk):
- **Bash is not in the blocked-tool set.** A teammate determined to bypass
  can run arbitrary shell commands. Blocking Bash would break the
  recovery-of-last-resort pattern from `bootstrap_gate.py`.
- **State forgery via `TaskUpdate`.** A teammate could write
  `teachback_state = "active"` without also writing a valid
  `teachback_approved`. The gate mitigates via **content-presence precedence**
  (it reads `teachback_approved` content, not the state field), but the
  `TaskUpdate` call itself cannot be intercepted by any hook.
- **Adversarial orchestrator output.** Nothing prevents a compromised
  orchestrator process from writing its own `teachback_approved` dict that
  passes schema. The gate assumes honest-but-careless, not hostile.

**Why the honest-reframe is load-bearing**: past attempts to describe the
gate as "teachback enforcement" without this caveat produced misaligned
expectations ("the gate will catch malicious agents"). Framing the gate as
ritual enforcement for honest-but-careless output both (a) sets accurate
expectations and (b) prevents over-investment in closing the accepted-risk
surfaces (F1 and F3 in RISK-MAP.md).

---

## Teachback Gate State Machine (issue #401)

The teachback protocol, once enforced mechanically by `teachback_gate.py`,
is a **cooperative 4-state machine**. "Cooperative" means state transitions
happen via `TaskUpdate` writes by teammates and lead — NOT via hook-enforced
atomic transitions. Claude Code's platform does not expose a `TaskUpdate`
hook event (F8 in RISK-MAP.md), so the gate's role is to read the current
state at `PreToolUse` and allow/deny, not to drive transitions.

### States (4)

| State | Semantics | Who is blocked? |
|---|---|---|
| `teachback_pending` | Task created; no `teachback_submit` yet | Teammate blocked on Edit/Write/Agent/NotebookEdit |
| `teachback_under_review` | Teammate submitted; lead has not approved/corrected | Teammate still blocked on same tool set |
| `active` | Lead approved with valid `teachback_approved` AND empty `unaddressed` list | Nobody blocked (normal work proceeds) |
| `teachback_correcting` | Lead requested corrections OR `teachback_approved.conditions_met.unaddressed` non-empty | Teammate blocked except for re-submission via TaskUpdate |

**Locked by TERMINOLOGY-LOCK.md**: the 4 names above are load-bearing. For
the full banned-alternatives list (superseded synonyms that must not appear
in code or new docs), see
`docs/architecture/teachback-gate/TERMINOLOGY-LOCK.md` §Banned terms.

### Transitions

Each transition is driven by a `TaskUpdate` write by either the teammate or
lead. The gate observes the transition on the next `PreToolUse` tool call
and emits a `teachback_state_transition` journal event.

| Transition | Driver | Writes to metadata | Gate observes |
|---|---|---|---|
| `teachback_pending` → `teachback_under_review` | Teammate | `teachback_submit` (valid) | ALLOW if schema-valid; DENY otherwise with per-field feedback |
| `teachback_under_review` → `active` | Lead | `teachback_approved` with empty `unaddressed` | ALLOW |
| `teachback_under_review` → `teachback_correcting` | Lead | `teachback_corrections` OR `teachback_approved` with non-empty `unaddressed` (auto-downgrade) | DENY teammate work, surface correction items |
| `teachback_correcting` → `teachback_under_review` | Teammate | updated `teachback_submit` addressing flagged fields | ALLOW cycle repeats (gate re-validates) |

**Carve-outs** (bypass the state machine entirely, by predicate order
locked in TERMINOLOGY-LOCK.md):
1. Signal tasks (`metadata.type in (blocker, algedonic)`)
2. Auditor/secretary signal tasks (`metadata.completion_type == "signal"`)
3. Skipped / stalled / terminated tasks
4. Exempt agents (`secretary`, `pact-secretary`, `auditor`, `pact-auditor`)
5. Low-variety tasks (`metadata.variety.total < TEACHBACK_BLOCKING_THRESHOLD = 7`)

### Protocol Levels (Q2 — full vs simplified)

The content schema for `teachback_submit` / `teachback_approved` has two
shapes gated on task variety + scope-items count:

- **Full protocol** — `metadata.variety.total >= 9` OR `len(required_scope_items) >= 2`
  - Required fields: `understanding`, `most_likely_wrong`, `least_confident_item`, `first_action`
- **Simplified protocol** — `variety in [7, 9)` AND `len(required_scope_items) < 2`
  - Required fields: `understanding`, `first_action` (only)
- **Exempt** — `variety < 7`: no teachback required (carve-out #5)

Full schemas with field-level validation rules live in
`docs/architecture/teachback-gate/CONTENT-SCHEMAS.md`. The gate enforces
validation at `PreToolUse` by reading `metadata.teachback_submit` and
applying the per-field rules (minimum length, not-template, citation regex,
substring-inequality against the teammate's own claims, membership checks
against `required_scope_items`).

### Revision Cycle (Q4 — targeted re-emission)

When the lead writes `teachback_corrections` with `request_revisions_on:
[field1, field2, ...]`, the teammate re-emits ONLY those fields via a new
`teachback_submit`. Unchanged fields are carried forward automatically;
the teammate does not re-write the entire submit. The gate re-validates
the whole submit on each cycle — no per-revision history.

### Relationship to Legacy `teachback_sent` Boolean

The legacy `metadata.teachback_sent` boolean (set by teammates pre-#401) is
preserved for Phase 1 backward compat but retired in Phase 3. During Phase
1 and Phase 2, `teachback_check.py` (PostToolUse advisory) and
`teachback_gate.py` (PreToolUse, advisory→blocking) run in parallel.
`teachback_gate.py` reads the richer `teachback_submit` dict and ignores
`teachback_sent`. New code MUST NOT introduce `teachback_sent` reads.

---
