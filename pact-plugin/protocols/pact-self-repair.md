## Self-Repair Protocol

> **Cybernetic basis**: Maturana & Varela's autopoiesis — a living system continuously regenerates
> its own components while maintaining its organizational identity. In PACT, the "organization"
> is the VSM structure (S1-S5 roles, phase sequence, coordination protocols); the "structure"
> is the current instantiation (active agents, tasks, session state).

Self-repair enables PACT to reconstitute its operational structure after disruptions (agent failures, session interruptions, context compaction) while preserving its organizational identity.

### Organization vs. Structure

| Concept | Definition | PACT Example | Survives Disruption? |
|---------|-----------|--------------|---------------------|
| **Organization** | Invariant pattern of relations | VSM roles, phase sequence P→A→C→T, coordination protocols | Yes (defined in protocols) |
| **Structure** | Current instantiation of relations | Which agents are running, current tasks, session state | No (must be reconstituted) |

**Key insight**: Self-repair reconstitutes *structure*, not organization. The organization is already defined in protocols and CLAUDE.md. Recovery means rebuilding the current state to match the invariant pattern.

### Pattern 1: Organizational State Snapshot (Prevention)

At defined checkpoints, capture a snapshot of the system's organizational state to enable recovery.

**When to capture**: At phase boundaries (same trigger as S4 checkpoints). Store in task metadata on the feature task via `TaskUpdate`.

**Snapshot fields**:
- `vsm_roles`: Which agents fill which VSM roles (S1 specialists, S2 conventions, S3 orchestrator state)
- `memory_layers`: Status of auto-memory, pact-memory, agent persistent memory
- `regulatory_mechanisms`: Which hooks/gates are active (completion gate, breadcrumb file, handoff validation)
- `phase_state`: Current phase, completed phases, pending work

**Recovery use**: After session interruption or context compaction, read the snapshot via `TaskGet` to reconstruct system state rather than inferring it from scattered signals.

### Pattern 2: Agent Boundary Reconstitution (Recovery)

When an agent fails (stall detected, context exhausted), spawn a replacement with recovered context.

**Steps**:
1. **Detect**: Stall detection (see [pact-agent-stall.md](pact-agent-stall.md)) identifies failed agent
2. **Assess**: Before spawning replacement, verify the *role* needs filling — if the phase has progressed past needing that specialist, don't replace
3. **Recover context**:
   - Extract partial work from failed agent's task metadata and file changes (`git diff`)
   - Query peer agent outputs via `TaskList`/`TaskGet` for context accumulated since failed agent was briefed
   - Check environment drift via `file-edits.json` for files modified since failure
4. **Spawn**: Create replacement agent with recovered context in dispatch prompt
5. **Verify**: After replacement starts, verify VSM structure is intact (all necessary roles filled, coordination protocols active)

**Extends**: pact-agent-stall.md (which handles detection and basic recovery). This protocol adds boundary awareness, enhanced context recovery, and organizational integrity verification.

### Recovery Context Sources

| Source | What It Provides | Access Method |
|--------|-----------------|---------------|
| Task system | Task states, metadata, handoffs | `TaskList`, `TaskGet` |
| Git state | Commits, branches, file changes | `git log`, `git diff`, `git worktree list` |
| pact-memory | Institutional knowledge, calibration data | Secretary query via `SendMessage` |
| Breadcrumb file | Temporal ordering of completions | Read `~/.claude/pact-sessions/{slug}/breadcrumbs.jsonl` |
| paused-state.json | Session checkpoint | Read `~/.claude/pact-sessions/{slug}/paused-state.json` |
| Organizational snapshot | VSM state at last checkpoint | `TaskGet(featureTaskId).metadata.org_snapshot` |
| Structured error output | Last hook failure context | Hook JSON output (see `error_output.py`) |

### Relationship to Other Protocols

- **Agent Stall Detection** ([pact-agent-stall.md](pact-agent-stall.md)): Detects failures; self-repair provides the recovery framework
- **S4 Checkpoints** ([pact-s4-checkpoints.md](pact-s4-checkpoints.md)): Question 5 (Conant-Ashby) assesses whether the model is adequate for regulation — self-repair acts when regulation has degraded
- **Channel Capacity** ([pact-channel-capacity.md](pact-channel-capacity.md)): Context compaction is a structural disruption; self-repair provides recovery patterns for post-compaction state reconstruction
- **State Recovery** (CLAUDE.md): Existing protocol provides the procedural steps; self-repair adds organizational awareness and verification

---
