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

When detection fires (score >= threshold), present the result using the S5 Decision Framing Protocol (see [pact-s5-policy.md](pact-s5-policy.md)) with icon `📐 Scope Change`. Offer three options: (A) Decompose into sub-scopes, (B) Continue as single scope, (C) Adjust boundaries.

| Response | Action |
|----------|--------|
| Confirmed (A) | Generate scope contracts (see [pact-scope-contract.md](pact-scope-contract.md)), then proceed to ATOMIZE phase |
| Rejected (B) | Continue single scope |
| Adjusted (C) | Generate scope contracts with modified boundaries, then ATOMIZE |

#### Autonomous Tier

Skip user confirmation when ALL strong signals fire, NO counter-signals present, and CLAUDE.md contains `autonomous-scope-detection: enabled`. Output: `Scope detection: Multi-scope (autonomous) — decomposing into [scope list]`

### Post-Detection: Scope Contract Generation

When decomposition is confirmed (by user or autonomous tier), the orchestrator generates a scope contract for each identified sub-scope before invoking rePACT. See [pact-scope-contract.md](pact-scope-contract.md) for the contract format and generation process.

---
