## Scope Detection

> **Purpose**: Define the heuristic signals, confidence model, and activation tiers
> for recognizing when a task exceeds single-scope capacity.
> Used by `orchestrate` (consumer) after PREPARE phase output analysis.
> See also: [Scoped Orchestration Design Doc](../../docs/plans/2026-02-01-v3-scoped-orchestration-design.md)

### Detection Heuristics

Detection evaluates PREPARE phase output (or approved plan content if PREPARE was skipped) against these signals. Each signal is binary (present/absent).

| # | Signal | Category | Evaluation Criteria |
|---|--------|----------|---------------------|
| S1 | Multiple distinct domains identified | Strong | PREPARE output names 2+ independent service/module domains |
| S2 | Different service boundaries referenced | Strong | Architecture mentions changes in separate service directories or repos |
| S3 | No shared files between domain groups | Supporting | File lists cluster cleanly into non-overlapping sets |
| S4 | Estimated agent count exceeds 4-5 | Supporting | Task would require specialists across multiple domains |
| S5 | Prior memory flags complexity | Supporting | pact-memory retrieval shows this feature was previously flagged as multi-scope |

### Counter-Signals

Counter-signals are **confidence demoters** (reduce confidence by one level), not vetoes. A multi-service task should still propose decomposition even with one shared type.

| # | Counter-Signal | Effect |
|---|----------------|--------|
| C1 | Shared data models or types across domains | Demotes confidence by one level |
| C2 | Small total scope despite multiple domains | Demotes confidence by one level (strongest counter-signal) |
| C3 | User explicitly invoked comPACT | Bypass detection entirely (flow concern, not scoring) |

### Confidence Matrix

Confidence is derived from signal combinations, then adjusted by counter-signals.

| Strong Signals | Supporting Signals | Counter-Signals | Base Confidence | Action |
|---|---|---|---|---|
| 0 | any | any | — | Single-scope (no detection triggered) |
| 1 | 0 | any | Low | Log observation, continue single-scope |
| 1 | 1+ | 0 | Medium | Propose decomposition (Confirmed tier) |
| 1 | 1+ | 1+ | Low-Medium | Demote confidence; may still propose if medium remains |
| 2+ | 1+ | 0 | High | Auto-decompose if Autonomous enabled; else Confirmed |
| 2+ | any | 1+ | Medium | Propose decomposition (Confirmed tier) |

Each counter-signal demotes the base confidence by one level (High → Medium → Low → no action). Multiple counter-signals stack.

### Detection Output Contract

The detection step produces a `ScopeAssessment` structure consumed by the orchestrate workflow:

```
ScopeAssessment:
  recommendation: "single-scope" | "multi-scope"
  confidence: "low" | "medium" | "high"
  signals_fired: [{name, category}]
  counter_signals_fired: [{name}]
  proposed_scopes: [  # only if multi-scope + medium/high confidence
    { name, domains: [], key_files: [], estimated_agents: N }
  ]
  shared_concerns: []  # cross-scope items requiring coordination
```

### Three-Tier Activation Model

| Tier | Behavior | Condition |
|------|----------|-----------|
| **Manual** | User invokes `/rePACT` explicitly | Always available (preserved, existing behavior) |
| **Confirmed** | Orchestrator proposes decomposition, user confirms | Default — detection fires with medium+ confidence |
| **Autonomous** | Orchestrator decomposes without asking | All strong signals fire, high confidence, no counter-signals. Disabled by default. |

**Tier precedence**: C3 counter-signal (comPACT invocation) bypasses detection entirely. Otherwise, the tier is determined by confidence level and autonomous configuration.

**Interim behavior (Phase B)**: When detection fires but Phase C execution infrastructure does not exist yet, the orchestrator notes the recommendation and falls back to single-scope execution. If the user confirms decomposition, the orchestrator uses manual `/rePACT` invocations for each proposed scope as a bridge.

### Autonomous Tier

The Autonomous tier allows the orchestrator to decompose without user confirmation when conditions are unambiguous. It is **disabled by default** and must be explicitly enabled.

**Configuration**: Add the following to the project's `CLAUDE.md`:

```markdown
### Scope Detection
- Autonomous decomposition: enabled
```

The orchestrator checks for this setting during scope detection. If absent or set to any other value, the Autonomous tier is inactive and all multi-scope recommendations route through the Confirmed tier (user approval required).

**Activation conditions** (ALL must be true):
1. Autonomous decomposition is enabled in `CLAUDE.md`
2. All strong signals (S1 and S2) have fired
3. Base confidence is High (2+ strong signals with 1+ supporting)
4. Zero counter-signals are present
5. comPACT was not invoked (C3 not active)

**Behavior when activated**: The orchestrator proceeds directly to decomposition without presenting the S5 Decision Framing template. It logs the auto-decomposition decision for auditability:

```
Scope Detection: Auto-decomposed into {N} scopes (Autonomous tier).
Signals: {signals fired}. Confidence: High. Counter-signals: none.
Scopes: {scope list}
```

**Safeguards**:
- If ANY counter-signal is present, fall back to Confirmed tier regardless of autonomous setting
- The user can always override by responding to any subsequent coordination step
- Autonomous decisions are logged identically to confirmed decisions for traceability

---
