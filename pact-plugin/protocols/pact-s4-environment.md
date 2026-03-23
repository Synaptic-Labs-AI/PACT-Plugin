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

### Dynamic Model Update Triggers

Beyond manual updates, certain runtime signals should trigger automatic model reassessment:

| Trigger | Source | Model Update |
|---------|--------|-------------|
| S2 semantic overlap detected | S2 coordination layer | Add newly discovered interface dependencies to External Dependencies |
| Agent blocker on missing dependency | imPACT triage | Add dependency to model; reassess Constraints |
| Calibration drift > 2 in any dimension | Calibration feedback loop | Re-examine Unknowns — systematic blind spot likely |
| Auditor RED signal | Concurrent audit protocol | Cross-reference against model assumptions — architecture drift may indicate invalidated constraint |
| 3+ imPACT cycles | Algedonic META-BLOCK | Model likely insufficient — trigger full model review |

**Integration with Conant-Ashby**: When S4 checkpoint question 5 (Model Completeness) detects degraded regulation, dynamic triggers ensure the environment model updates reflect the gap. The model must stay current for effective regulation — stale models produce stale regulation.

### Relationship to S4 Checkpoints

The Environment Model is the baseline against which S4 checkpoints assess:
- "Environment shifted" → Compare current state to Environment Model
- "Model diverged" → Assumptions in model no longer hold
- "Plan viable" → Constraints in model still valid for current approach

---
