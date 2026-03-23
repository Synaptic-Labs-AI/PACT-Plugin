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
- Enable parallel execution (primary CODE phase strategy; use QDCL from orchestrate.md)
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

At orchestration completion (wrap-up), the orchestrator captures a calibration record comparing initial variety assessment against actual difficulty. Records are saved to pact-memory via the secretary and feed back into Learning II pattern matching.

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

**Post-cycle comparison**: At wrap-up, the orchestrator:
1. Compares initial variety score vs. actual difficulty
2. Identifies dimensions that drifted (predicted vs. actual)
3. Notes blocker count and phase reruns as difficulty indicators
4. Saves calibration record to pact-memory via secretary task
5. If drift exceeds 2 in any dimension, note as significant for future Learning II queries

### Calibration Feedback Loop

> **Cybernetic basis**: Bateson's deutero-learning extended — beyond pattern detection (Learning II),
> the system uses quantitative calibration data to auto-adjust scoring with damping.

The calibration feedback loop provides automatic variety score adjustment based on accumulated calibration records. It operates alongside Learning II (qualitative pattern matching) as a quantitative complement.

**Two feedback layers**:

| Layer | Type | Activation | Effect |
|-------|------|-----------|--------|
| **Learning II** | Qualitative (pattern matching) | 5+ matching pact-memory entries for domain | +1 to relevant dimension |
| **Calibration feedback** | Quantitative (drift measurement) | 5+ calibration records for domain | +/-1 to total score |

**Algorithm** (windowed average with domain scoping):
1. Filter calibration records by task domain
2. Take most recent 5 records (window)
3. If fewer than 5 records: no adjustment (cold start)
4. Compute drift = mean(actual_difficulty - initial_variety) across window
5. If abs(drift) < 1.0: no adjustment (within noise threshold)
6. Adjustment = clamp(round(drift), -1, +1)
7. Apply to base score, clamped to valid range (4-16)

**Parameters**:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Window size | 5 | Balances recency with stability |
| Minimum samples | 5 per domain | Prevents cold-start overcorrection |
| Max adjustment | +/-1 total | Prevents large jumps from single feedback cycle |
| Noise threshold | 1.0 | Drift below this is random variation, not signal |
| Domain scoping | Records keyed by domain string | "auth" calibrations don't affect "frontend" scoring |

**Application order**: Learning II adjustment first (dimension-level), then calibration feedback (score-level). Both adjustments are clamped independently.

**Cold-start behavior**: When a domain has fewer than 5 calibration records, Learning II (qualitative) may still fire if 5+ memories match. The system has two independent activation paths — either can provide value alone.

---
