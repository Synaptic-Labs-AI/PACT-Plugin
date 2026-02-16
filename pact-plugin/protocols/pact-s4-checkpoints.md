## S4 Checkpoint Protocol

At phase boundaries, the orchestrator performs an S4 checkpoint to assess whether the current approach remains valid.

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

S4 Checkpoints complement Variety Checkpoints (see Variety Management):
- **Variety Checkpoints**: "Do we have enough response capacity for this complexity?"
- **S4 Checkpoints**: "Is our understanding of the situation still valid?"

Both occur at phase transitions but ask different questions.

### S3/S4 Tension Detection

At checkpoints, watch for tension between operational pressure (S3) and strategic caution (S4):

**Common tensions:**
- Schedule vs Quality (skip phases vs thorough work)
- Execute vs Investigate (code now vs understand first)
- Commit vs Adapt (stay course vs change approach)

**Detection phrases** — if you think these, pause:
- "We're behind, let's skip PREPARE" → S3 pushing
- "Let's just code it and see" → S3 shortcutting
- "This feels risky, we should plan more" → S4 cautioning

**Resolution:** Name the tension, articulate trade-offs for each path, resolve based on project values. If unclear, escalate to user using S5 Decision Framing.

---
