## Scoped Phases (ATOMIZE and CONSOLIDATE)

> **Purpose**: Define the scoped orchestration phases used when decomposition creates sub-scopes.
> These phases replace the standard ARCHITECT and CODE phases when scope detection fires.
> For single-scope workflows, these phases are skipped entirely.

### ATOMIZE Phase

**Skip criteria**: No decomposition occurred (no scope contracts generated) → Proceed to CONSOLIDATE phase.

This phase spawns teammates for independent sub-scope execution. Each sub-scope teammate runs a full PACT cycle (Prepare → Architect → Code → Test) within its assigned worktree.

**Worktree isolation**: Before spawning sub-scope teammates, create an isolated worktree for each:
1. Invoke `/PACT:worktree-setup` with suffix branch: `feature-X--{scope_id}`
2. Pass the worktree path in the teammate spawn prompt so the sub-scope operates in its own filesystem

**Dispatch**: Spawn one teammate per sub-scope into the session team:
```
Task(
    subagent_type="{specialist}",
    team_name="{team}",
    name="scope-{scope_id}-{role}",
    mode="plan",
    prompt="Scope Contract: {scope_id}\n...\nWorktree: {worktree_path}"
)
```
Sub-scope teammates run concurrently (default) unless they share files. When generating scope contracts, ensure `shared_files` constraints are set per the generation process in [pact-scope-contract.md](pact-scope-contract.md) — sibling scopes must not modify each other's owned files.

**Sub-scope failure policy**: Sub-scope failure is isolated — sibling scopes continue independently. Individual scope failures route through `/PACT:imPACT` to the affected scope only. However, when a sub-scope teammate emits HALT (dual-delivery: direct to lead + broadcast to peers), the lead stops ALL sub-scope teammates (consistent with algedonic protocol: "Stop ALL teammates"). Preserve work-in-progress for all scopes. After HALT resolution, review interrupted scopes before resuming.

**Before next phase**:
- [ ] All sub-scope teammates have sent HANDOFF via SendMessage
- [ ] Contract fulfillment sections received from all sub-scopes
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **S4 Checkpoint**: All scopes delivered? Any scope stalled?
- [ ] Shut down all sub-scope teammates before proceeding

---

### CONSOLIDATE Phase

**Skip criteria**: No decomposition occurred → Proceed to TEST phase.

This phase verifies that independently-developed sub-scopes are compatible before comprehensive testing.

**Merge sub-scope branches**: Before running contract verification, merge each sub-scope's work back:
1. For each completed sub-scope, merge its suffix branch to the feature branch
2. Invoke `/PACT:worktree-cleanup` for each sub-scope worktree
3. Proceed to contract verification and integration tests (below) on the merged feature branch

**Spawn consolidation teammates in parallel**:
- **`pact-architect`** (name: `"consolidate-architect"`): Verify cross-scope contract compatibility
  - Compare contract fulfillment sections from all sub-scope handoffs
  - Check that exports from each scope match imports expected by siblings
  - Flag interface mismatches, type conflicts, or undelivered contract items
- **`pact-test-engineer`** (name: `"consolidate-tester"`): Run cross-scope integration tests
  - Verify cross-scope interfaces work together (API calls, shared types, data flow)
  - Test integration points identified in scope contracts
  - Confirm no shared file constraint violations occurred

**Include in each teammate's spawn prompt**:
- Feature description and scope contract summaries
- All sub-scope handoffs (contract fulfillment sections)
- "This is cross-scope integration verification. Focus on compatibility between scopes, not internal scope correctness."

**On consolidation failure**: Route through `/PACT:imPACT` for triage. Possible outcomes:
- Interface mismatch → spawn a coder teammate to fix the affected scope
- Contract deviation → architect reviews whether deviation is acceptable
- Test failure → test engineer provides details, spawn coder to fix

**Before next phase**:
- [ ] Cross-scope contract compatibility verified
- [ ] Integration tests passing
- [ ] Teammate HANDOFF(s) received via SendMessage
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of CONSOLIDATE phase work
- [ ] Shut down consolidation teammates
- [ ] **S4 Checkpoint**: Scopes compatible? Integration clean? Plan viable?

---

### Related Protocols

- [pact-scope-detection.md](pact-scope-detection.md) — Heuristics for detecting multi-scope tasks
- [pact-scope-contract.md](pact-scope-contract.md) — Contract format and lifecycle

---
