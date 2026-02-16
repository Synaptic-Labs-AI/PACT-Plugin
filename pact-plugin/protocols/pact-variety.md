## Variety Management

Variety = task complexity. Assess before choosing a workflow.

### Complexity Dimensions

Score each dimension 1-4:

| Dimension | 1 (Low) | 2 (Medium) | 3 (High) | 4 (Extreme) |
|-----------|---------|------------|----------|-------------|
| **Novelty** | Routine (done before) | Familiar (similar to past) | Novel (new territory) | Unprecedented |
| **Scope** | Single concern | Few concerns | Many concerns | Cross-cutting |
| **Uncertainty** | Clear requirements | Mostly clear | Ambiguous | Unknown |
| **Risk** | Low impact if wrong | Medium impact | High impact | Critical |

### Workflow Selection

Sum the four scores:

| Score | Workflow |
|-------|----------|
| **4-6** | `/PACT:comPACT` |
| **7-10** | `/PACT:orchestrate` |
| **11-14** | `/PACT:plan-mode` → `/PACT:orchestrate` |
| **15-16** | Research spike → Reassess |

**Calibration Examples**:

| Task | N | S | U | R | Score | Workflow |
|------|---|---|---|---|-------|----------|
| Add pagination to existing list endpoint | 1 | 1 | 1 | 2 | **5** | comPACT |
| Add new CRUD endpoints following existing patterns | 1 | 2 | 1 | 2 | **6** | comPACT |
| Implement OAuth with new identity provider | 3 | 3 | 3 | 3 | **12** | plan-mode → orchestrate |
| Rewrite auth system with unfamiliar framework | 4 | 4 | 4 | 4 | **16** | Research spike |

### Complexity Strategies

When complexity is high:
- Decompose into smaller, well-scoped sub-tasks
- Add more specialists or enable parallel execution
- Invoke nested PACT (`/PACT:rePACT`) for complex sub-components
- Run PREPARE phase to build understanding

### Phase-Boundary Check

At phase transitions, briefly ask: "Has complexity changed since we started?"
- Increased → Consider adding specialists or nested PACT
- Decreased → Consider simplifying (fewer agents, skip phases)
- Stable → Continue as planned

---
