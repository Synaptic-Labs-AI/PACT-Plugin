## Scoped Phases (ATOMIZE and CONSOLIDATE)

> **Purpose**: Define the scoped orchestration phases used when decomposition creates sub-scopes.
> These phases replace the standard ARCHITECT and CODE phases when scope detection fires.
> For single-scope workflows, these phases are skipped entirely.

### ATOMIZE Phase

**Skip criteria**: No decomposition occurred (no scope contracts generated) → Proceed to CONSOLIDATE phase.

This phase dispatches sub-scopes for independent execution. Each sub-scope runs a full PACT cycle (Prepare → Architect → Code → Test) via rePACT.

**Worktree isolation**: Before dispatching sub-scopes, create an isolated worktree for each:
1. Invoke `/PACT:worktree-setup` with suffix branch: `feature-X--{scope_id}`
2. Pass the worktree path to the rePACT invocation so the sub-scope operates in its own filesystem

**Dispatch**: Invoke `/PACT:rePACT` for each sub-scope with its scope contract and worktree path. Sub-scopes run concurrently (default) unless they share files. When generating scope contracts, ensure `shared_files` constraints are set per the generation process in [pact-scope-contract.md](pact-scope-contract.md) -- sibling scopes must not modify each other's owned files.

**Sub-scope failure policy**: Sub-scope failure is isolated — sibling scopes continue independently. Individual scope failures route through `/PACT:imPACT` to the affected scope only. However, when a sub-scope emits HALT, the parent orchestrator stops ALL sub-scopes (consistent with algedonic protocol: "Stop ALL agents"). Preserve work-in-progress for all scopes. After HALT resolution, review interrupted scopes before resuming.

**Before next phase**:
- [ ] All sub-scope rePACT cycles complete
- [ ] Contract fulfillment sections received from all sub-scopes
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **S4 Checkpoint**: All scopes delivered? Any scope stalled?

---

### CONSOLIDATE Phase

**Skip criteria**: No decomposition occurred → Proceed to TEST phase.

This phase verifies that independently-developed sub-scopes are compatible before comprehensive testing.

**Merge sub-scope branches**: Before running contract verification, merge each sub-scope's work back:
1. For each completed sub-scope, merge its suffix branch to the feature branch
2. Invoke `/PACT:worktree-cleanup` for each sub-scope worktree
3. Proceed to contract verification and integration tests (below) on the merged feature branch

**Delegate in parallel**:
- **`pact-architect`**: Verify cross-scope contract compatibility
  - Compare contract fulfillment sections from all sub-scope handoffs
  - Check that exports from each scope match imports expected by siblings
  - Flag interface mismatches, type conflicts, or undelivered contract items
- **`pact-test-engineer`**: Run cross-scope integration tests
  - Verify cross-scope interfaces work together (API calls, shared types, data flow)
  - Test integration points identified in scope contracts
  - Confirm no shared file constraint violations occurred

**Invoke each with**:
- Feature description and scope contract summaries
- All sub-scope handoffs (contract fulfillment sections)
- "This is cross-scope integration verification. Focus on compatibility between scopes, not internal scope correctness."

**On consolidation failure**: Route through `/PACT:imPACT` for triage. Possible outcomes:
- Interface mismatch → re-invoke affected scope's coder to fix
- Contract deviation → architect reviews whether deviation is acceptable
- Test failure → test engineer provides details, coder fixes

**Before next phase**:
- [ ] Cross-scope contract compatibility verified
- [ ] Integration tests passing
- [ ] Specialist handoff(s) received
- [ ] If blocker reported → `/PACT:imPACT`
- [ ] **Create atomic commit(s)** of CONSOLIDATE phase work
- [ ] **S4 Checkpoint**: Scopes compatible? Integration clean? Plan viable?

---

### Related Protocols

- [pact-scope-detection.md](pact-scope-detection.md) — Heuristics for detecting multi-scope tasks
- [pact-scope-contract.md](pact-scope-contract.md) — Contract format and lifecycle
- [rePACT.md](../commands/rePACT.md) — Recursive PACT command for sub-scope execution

---
