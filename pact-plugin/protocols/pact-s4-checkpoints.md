## S4 Checkpoint Protocol

At phase boundaries, the orchestrator performs an S4 checkpoint to assess whether the current approach remains valid.

> **Temporal Horizon**: S4 operates at a **days** horizon—asking questions about the current milestone or sprint, not minute-level implementation details. See `CLAUDE.md > Temporal Horizons` for the full horizon model.

### Trigger Points

- After PREPARE phase completes
- After ARCHITECT phase completes
- After CODE phase completes (before TEST)
- When any agent reports unexpected complexity
- On user-initiated "pause and assess"

### Checkpoint Questions

1. **Environment Change**: Has external context shifted?
   - New requirements discovered?
   - Constraints invalidated?
   - Dependencies changed?

2. **Model Divergence**: Does our understanding match reality?
   - Assumptions proven wrong?
   - Estimates significantly off?
   - Risks materialized or emerged?

3. **Plan Viability**: Is current approach still optimal?
   - Should we continue as planned?
   - Adapt the approach?
   - Escalate to user for direction?

### Checkpoint Outcomes

| Finding | Action |
|---------|--------|
| All clear | Continue to next phase |
| Minor drift | Note in handoff, continue |
| Significant change | Pause, assess, may re-run prior phase |
| Fundamental shift | Escalate to user (S5) |

### Checkpoint Format (Brief)

> **S4 Checkpoint** [Phase→Phase]:
> - Environment: [stable / shifted: {what}]
> - Model: [aligned / diverged: {what}]
> - Plan: [viable / adapt: {how} / escalate: {why}]

### Output Behavior

**Default**: Silent-unless-issue — checkpoint runs internally; only surfaces to user when drift or issues detected.

**Examples**:

*Silent (all clear)*:
> (Internal) S4 Checkpoint Post-PREPARE: Environment stable, model aligned, plan viable → continue

*Surfaces to user (issue detected)*:
> **S4 Checkpoint** [PREPARE→ARCHITECT]:
> - Environment: Shifted — API v2 deprecated, v3 has breaking changes
> - Model: Diverged — Assumed backwards compatibility, now false
> - Plan: Adapt — Need PREPARE extension to research v3 migration path

### Relationship to Variety Checkpoints

S4 Checkpoints complement Variety Checkpoints (see [Variety Management](pact-variety.md)):
- **Variety Checkpoints**: "Do we have enough response capacity for this complexity?"
- **S4 Checkpoints**: "Is our understanding of the situation still valid?"

Both occur at phase transitions but ask different questions.

---
