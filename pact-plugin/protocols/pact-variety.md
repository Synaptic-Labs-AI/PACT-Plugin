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

#### Per-Dispatch Variety Stamping

The feature-level CalibrationRecord above coexists with per-dispatch variety stamping. Every primary work task (Task B in the Teachback-Gated Dispatch shape) receives `metadata.variety` at `TaskCreate`-time using the per-dimension rationale schema. The orchestrator scores THIS dispatch's complexity afresh — NOT inherited from feature variety, NOT capped by feature variety.

**Per-dispatch schema** (stamped at TaskCreate-time on each Task B):

```
{
  "variety": {
    "novelty":               1-4,
    "novelty_rationale":     "<1-sentence: why this score for THIS dispatch's novelty>",
    "scope":                 1-4,
    "scope_rationale":       "<1-sentence: why this score for THIS dispatch's scope>",
    "uncertainty":           1-4,
    "uncertainty_rationale": "<1-sentence: why this score for THIS dispatch's uncertainty>",
    "risk":                  1-4,
    "risk_rationale":        "<1-sentence: why this score for THIS dispatch's risk>",
    "total":                 4-16
  }
}
```

**Why per-dimension rationales (not a single rationale)**: A single rationale field tolerates cargo-cult ("matches feature complexity" satisfies it). Four distinct rationale fields, one per dimension, force the orchestrator to articulate four independent judgments — cargo-culting all four with one phrase is mechanically incoherent (cannot coherently explain why novelty AND scope AND uncertainty AND risk are simultaneously "the same as feature" without exposing the copy-paste).

#### variety_acknowledgment — Teammate Verification Workflow

The teammate becomes the peer reviewer of the orchestrator's variety scoring. The teachback canonical schema includes a required `variety_acknowledgment` sub-field stored alongside the 4 existing teachback fields:

```
"variety_acknowledgment": {
  "rationale_articulates_this_dispatch": "yes" | "no" | "concern",
  "concern": "<required when value != 'yes'; names the smell>"
}
```

**Teammate workflow** (extends pact-teachback skill's Step 1 metadata write):

1. After claiming Task A and reading the task description, the teammate reads `metadata.variety` on Task B (resolved via `Task A.blocks[0]`) BEFORE composing the teachback_submit payload.
2. Teammate judges each of the four per-dimension rationales against THIS dispatch's actual work — does `novelty_rationale` articulate why THIS dispatch is novel, or does it copy feature-level language? Same check for `scope_rationale`, `uncertainty_rationale`, `risk_rationale`.
3. Teammate records the judgment in `metadata.teachback_submit.variety_acknowledgment`:
   - `"yes"` — all four rationales articulate THIS dispatch's complexity; `concern` field omitted or empty.
   - `"no"` — one or more rationales appear cargo-culted or wrong; teammate names the smell in `concern`.
   - `"concern"` — softer signal; teammate has reservation but not certain; names the doubt in `concern`.

**Lead workflow** (extends teachback review):

The lead reviews `variety_acknowledgment` as part of teachback acceptance per [pact-completion-authority.md §Teachback Review](pact-completion-authority.md#teachback-review). Two acceptance paths:

- **`"yes"`**: standard teachback acceptance; lead marks Task A completed + sends paired wake-SendMessage.
- **`"no"` or `"concern"`**: lead has two corrective options before acceptance:
  - *Orchestrator-side correction* (preferred when teammate's flag is correct): re-stamp `metadata.variety` on Task B via TaskUpdate with refined per-dimension rationales, THEN accept the teachback. The teammate's acknowledgment becomes part of the audit trail; no rejection needed.
  - *Teammate-side correction* (when teammate's flag is erroneous): reject the teachback via `metadata.teachback_rejection` with reason explaining why the variety scoring stands as-is; teammate revises and resubmits.

**META-BLOCK escalation at 3+ rejection cycles**: if teammate flags persist across 3+ cycles after lead correction attempts, the standard imPACT META-BLOCK escalation applies — see [pact-completion-authority.md §META-BLOCK](pact-completion-authority.md#meta-block). The 3-cycle bound is the existing protocol's bound; per-dispatch variety stamping inherits, does not redefine.

#### Variety Acknowledgment Signal (Wrap-Up Aggregation)

At wrap-up time, the secretary aggregates `variety_acknowledgment` flag rates across the session's dispatch corpus. Two triggers surface a calibration concern in the orchestration retrospective:

- **Rate trigger**: if more than 20% of teachbacks recorded `"no"` or `"concern"`, flag the orchestrator's variety scoring as potentially miscalibrated for this session's dispatch shape.
- **Single-no trigger**: a single `"no"` flag (stronger signal than `"concern"`) on a load-bearing dispatch surfaces the specific dispatch + smell in the retrospective, even when rate-trigger does not fire.

The aggregation feeds back into Learning II calibration data alongside the feature-level CalibrationRecord — per-dispatch acknowledgment rates are a leading indicator of orchestrator-side scoring drift.

---
