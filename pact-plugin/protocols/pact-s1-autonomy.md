## S1 Autonomy & Recursion

Specialists (S1) have bounded autonomy to adapt within their domain. This section defines those boundaries and enables recursive PACT cycles for complex sub-tasks.

### Autonomy Charter

All specialists have authority to:
- **Adjust implementation approach** based on discoveries during work
- **Request context** from other specialists via the orchestrator
- **Recommend scope changes** when task complexity differs from estimate
- **Apply domain expertise** without micro-management from orchestrator

All specialists must escalate when:
- **Discovery contradicts architecture** — findings invalidate the design
- **Scope change exceeds 20%** — significantly more/less work than expected
- **Security/policy implications emerge** — potential S5 violations discovered
- **Cross-domain dependency** — need changes in another specialist's area

### Self-Coordination

When working in parallel (see [S2 Coordination](pact-s2-coordination.md#s2-coordination-layer)):
- Check S2 protocols before starting if multiple agents are active
- Respect assigned file/component boundaries
- First agent's conventions become standard for the batch
- Report potential conflicts to orchestrator immediately

### Recursive PACT (Nested Cycles)

When a sub-task is complex enough to warrant its own PACT treatment:

**Recognition Indicators:**
- Sub-task spans multiple concerns within your domain
- Sub-task has its own uncertainty requiring research
- Sub-task output feeds multiple downstream consumers
- Sub-task could benefit from its own prepare/architect/code/test cycle

**Protocol:**
1. **Declare**: "Invoking nested PACT for {sub-task}"
2. **Execute**: Run mini-PACT cycle (may skip phases if not needed)
3. **Integrate**: Merge results back to parent task
4. **Report**: Include nested work in handoff to orchestrator

**Constraints:**
- **Nesting limit**: 1 level maximum (prevent infinite recursion)
- **Scope check**: Nested PACT must be within your domain; cross-domain needs escalate to orchestrator
- **Documentation**: Nested cycles report via handoff to parent
- **Algedonic signals**: Algedonic signals from nested cycles still go **directly to user**—they bypass both the nested orchestration AND the parent orchestrator. Viability threats don't wait for hierarchy.

**Example:**
```
Parent task: "Implement user authentication service"
Nested PACT: "Research and implement OAuth2 token refresh mechanism"
  - Mini-Prepare: Research OAuth2 refresh token best practices
  - Mini-Architect: Design token storage and refresh flow
  - Mini-Code: Implement the mechanism
  - Mini-Test: Smoke test the refresh flow
```

### Orchestrator-Initiated Recursion (/PACT:rePACT)

While specialists can invoke nested cycles autonomously, the orchestrator can also initiate them:

| Initiator | Mechanism | When |
|-----------|-----------|------|
| Specialist | Autonomy Charter | Discovers complexity during work |
| Orchestrator | `/PACT:rePACT` command | Identifies complex sub-task upfront |

**Usage:**
- Single-domain: `/PACT:rePACT backend "implement rate limiting"`
- Multi-domain: `/PACT:rePACT "implement audit logging sub-system"`

See [rePACT.md](../commands/rePACT.md) for full command documentation.

---

### Teachback Gate Expectations (issue #401)

Autonomy is bounded by the teachback gate when the agent's task is
variety-scored at or above `TEACHBACK_BLOCKING_THRESHOLD` (=7, see
[pact-variety.md §Gate Thresholds](pact-variety.md#gate-thresholds-teachback-gate-issue-401)).
Specialists must:

1. **Write `teachback_submit` before Edit/Write/Agent/NotebookEdit** — not
   just send a SendMessage. The gate reads `metadata.teachback_submit`
   via `TaskUpdate`, not the message stream. SendMessage alone no longer
   satisfies the gate for variety >= 7 tasks.
2. **Respect `teachback_corrections`** — when the lead writes
   corrections, re-emit only the flagged fields per
   `request_revisions_on`. Unchanged fields carry forward automatically
   (see [pact-ct-teachback.md §Revision Cycle](pact-ct-teachback.md#revision-cycle-q4-targeted-re-emission)).
3. **Address `required_scope_items`** — every scope item named in the
   dispatch metadata must appear in either the teachback's
   `understanding` / `most_likely_wrong` / `least_confident_item`
   fields or in the completion HANDOFF's equivalent sections.
   `teachback_approved.conditions_met.unaddressed` will be non-empty
   until all items are addressed, auto-downgrading the state to
   `teachback_correcting`.
4. **Do NOT rationalize the gate away.** The gate enforces ritual for
   honest-but-careless output. An agent that soft-loops ("I'll skip the
   full teachback because the task is self-explanatory") violates the
   autonomy boundary. If the scope genuinely doesn't warrant a teachback,
   raise that as a scope-change signal to the orchestrator — do not
   self-exempt.

**Exempt agents** (carve-out, no teachback required at any variety):
`secretary`, `pact-secretary`, `auditor`, `pact-auditor`. These agents
either receive instructions via SendMessage (secretary briefings) or
produce signal-shaped outputs (auditor findings) that don't fit the
conversation-continuation model.

**Signal tasks** (blocker, algedonic, skipped, stalled, terminated) also
bypass the gate — those communicate via different channels and are
structurally incompatible with the teachback workflow.

---
