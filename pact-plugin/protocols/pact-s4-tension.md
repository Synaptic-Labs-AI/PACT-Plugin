## S3/S4 Tension Detection and Resolution

S3 (operational control) and S4 (strategic intelligence) are in constant tension. This is healthy—but unrecognized tension leads to poor decisions.

### Tension Indicators

S3/S4 tension exists when:
- **Schedule vs Quality**: Pressure to skip phases vs need for thorough work
- **Execute vs Investigate**: Urge to code vs need to understand
- **Commit vs Adapt**: Investment in current approach vs signals to change
- **Efficiency vs Safety**: Speed of parallel execution vs coordination overhead

### Detection Phrases

When you find yourself thinking:
- "We're behind, let's skip PREPARE" → S3 pushing
- "Requirements seem unclear, we should dig deeper" → S4 pulling
- "Let's just code it and see" → S3 shortcutting
- "This feels risky, we should plan more" → S4 cautioning

### Resolution Protocol

1. **Name the tension explicitly**:
   > "S3/S4 tension detected: [specific tension]"

2. **Articulate trade-offs**:
   > "S3 path: [action] — gains: [X], risks: [Y]"
   > "S4 path: [action] — gains: [X], risks: [Y]"

3. **Assess against project values**:
   - Does [CLAUDE.md](../CLAUDE.md) favor speed or quality for this project?
   - Is this a high-risk area requiring caution?
   - What has the user expressed preference for?

4. **If resolution is clear**: Decide and document
5. **If resolution is unclear**: Escalate to user (S5)

### Escalation Format

When escalating S3/S4 tension to user, use S5 Decision Framing:

> ⚖️ **S3/S4 Tension**: {One-line summary}
>
> **Context**: [What's happening, why tension exists]
>
> **Option A (S3 — Operational)**: [Action]
> - Gains: [Benefits]
> - Risks: [Costs]
>
> **Option B (S4 — Strategic)**: [Action]
> - Gains: [Benefits]
> - Risks: [Costs]
>
> **Recommendation**: [If you have one, with rationale]

### Integration with S4 Checkpoints

S4 Checkpoints are natural points to assess S3/S4 tension:
- Checkpoint finds drift → S3 wants to continue, S4 wants to adapt → Tension
- Checkpoint finds all-clear but behind schedule → S3 wants to skip phases, S4 wants thoroughness → Tension

When a checkpoint surfaces tension, apply the Resolution Protocol above.

---
