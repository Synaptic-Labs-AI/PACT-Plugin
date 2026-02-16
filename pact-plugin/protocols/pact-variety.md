## Variety Management

Variety = task complexity. Assess before choosing a workflow.

### Complexity Dimensions

When evaluating a task, consider four dimensions:

| Dimension | Low | High |
|-----------|-----|------|
| **Novelty** | Routine, done before | Novel or unprecedented |
| **Scope** | Single concern | Cross-cutting, many concerns |
| **Uncertainty** | Clear requirements | Ambiguous or unknown |
| **Risk** | Low impact if wrong | Critical impact |

### Workflow Selection

| Task Profile | Workflow |
|-------------|----------|
| Simple, routine, clear, low-risk | `/PACT:comPACT` |
| Multi-concern, some novelty or uncertainty | `/PACT:orchestrate` |
| Complex, novel, uncertain, or high-risk | `/PACT:plan-mode` → `/PACT:orchestrate` |
| Unprecedented across all dimensions | Research spike → Reassess |

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
