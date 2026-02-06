# Integration Test Scenario: Scoped Orchestration

**Priority**: P1
**Type**: Integration (runbook)
**Estimated Time**: 45-60 minutes

## Scenario Description

Verify the scoped orchestration flow: scope detection fires after PREPARE, the orchestrator proposes decomposition, sub-scopes execute via rePACT in isolated worktrees, and CONSOLIDATE merges and verifies cross-scope compatibility. This tests the full PREPARE, ATOMIZE, CONSOLIDATE, TEST scoped pipeline.

## Prerequisites

1. PACT plugin installed with all orchestration commands and protocols
2. `CLAUDE.md` contains `autonomous-scope-detection: enabled` (for testing the autonomous tier) or leave it out (for testing the confirmed tier)
3. No active worktrees for the test feature branch
4. Familiarity with `pact-plugin/protocols/pact-scope-detection.md` (scoring heuristics) and `pact-plugin/protocols/pact-scope-phases.md` (ATOMIZE/CONSOLIDATE phases)
5. All verification scripts passing

## Steps

### Step 1: Invoke Orchestration with Multi-Domain Task

Choose a task that touches 2+ independent domains to trigger scope detection. Example:

```
/PACT:orchestrate Build a user profile feature: REST API endpoint for profile CRUD, React component for profile display, and database migration for the profiles table
```

This task has distinct domain boundaries (backend API, frontend UI, database migration) and non-overlapping work areas -- both strong signals in the detection heuristics.

### Step 2: Observe PREPARE Phase

**What happens**: PREPARE always runs in single scope. The preparer researches requirements across all domains.

**Expected outcome**:
- `pact-preparer` dispatched as a background agent
- Research output produced in `docs/preparation/` within the worktree
- HANDOFF returned with requirements spanning multiple domains

### Step 3: Observe Scope Detection

**What happens**: After PREPARE completes, the orchestrator evaluates the output against the heuristics in `pact-plugin/protocols/pact-scope-detection.md`.

**Expected scoring** for the example task:
| Signal | Points |
|--------|--------|
| Distinct domain boundaries (backend + frontend + database) | 2 |
| Non-overlapping work areas (API files, React files, migration files) | 2 |
| High specialist count (3+ specialists needed) | 1 |
| **Total** | **5** |

Score 5 >= threshold 3 -- detection fires.

**Expected outcome** depends on activation tier:

**Confirmed tier** (default, `autonomous-scope-detection` not in CLAUDE.md):
```
Scope detection: Multi-scope detected (score 5/3 threshold) -- proposing decomposition

Scope Change: Multi-scope task detected

Context: 3 distinct domains identified (backend API, frontend UI, database migration) with no shared files.

Options:
A) Decompose into sub-scopes: [backend, frontend, database]
   - Trade-off: Better isolation, parallel execution; overhead of scope coordination

B) Continue as single scope
   - Trade-off: Simpler coordination; risk of context overflow with large task

C) Adjust boundaries (specify)

Recommendation: A -- 3 independent domains with no shared files
```

**Autonomous tier** (if `autonomous-scope-detection: enabled` in CLAUDE.md and both strong signals fire with no counter-signals):
```
Scope detection: Multi-scope (autonomous) -- decomposing into [backend, frontend, database]
```

Select **Option A** (or let autonomous proceed) to continue the scenario.

### Step 4: Observe Scope Contract Generation

**What happens**: The orchestrator generates a scope contract for each sub-scope per `pact-plugin/protocols/pact-scope-contract.md`. Contracts define owned files, expected outputs, and cross-scope interfaces.

**Expected outcome**: 3 scope contracts generated:
- Backend scope: owns API endpoint files, exports REST interface
- Frontend scope: owns React component files, imports REST interface
- Database scope: owns migration files, exports schema changes

### Step 5: Observe Task Hierarchy Update

**What happens**: The orchestrator creates scoped phase tasks:
- ATOMIZE task (blockedBy PREPARE)
- Per-scope rePACT tasks as children of ATOMIZE
- CONSOLIDATE task (blockedBy all scope tasks)
- TEST task updated: addBlockedBy CONSOLIDATE

Standard ARCHITECT and CODE tasks are marked `completed` with `{"skipped": true, "skip_reason": "decomposition_active"}`.

### Step 6: Observe ATOMIZE Phase

**What happens**: The orchestrator dispatches sub-scopes for execution. Each sub-scope gets:
1. Its own worktree via `/PACT:worktree-setup` (e.g., `feature/user-profile--backend`)
2. A `/PACT:rePACT` invocation with the scope contract
3. Independent execution of a full P, A, C, T cycle within its scope

**Expected outcome**:
- 3 worktrees created (one per sub-scope)
- 3 rePACT cycles running concurrently
- Each produces commits on its suffix branch

**Verification**:
```bash
git worktree list
```
Should show 4 entries: main directory + 3 sub-scope worktrees:
```
/path/to/repo                          main
/path/to/repo/.worktrees/feature/user-profile          feature/user-profile
/path/to/repo/.worktrees/feature/user-profile--backend  feature/user-profile--backend
/path/to/repo/.worktrees/feature/user-profile--frontend feature/user-profile--frontend
/path/to/repo/.worktrees/feature/user-profile--database feature/user-profile--database
```

### Step 7: Observe Sub-Scope Completion

**What happens**: Each rePACT cycle completes independently and returns a HANDOFF with contract fulfillment details.

**Expected outcome**: All 3 sub-scopes complete. Each reports:
- Files produced (within its owned scope)
- Contract items fulfilled
- Any cross-scope dependencies discovered

**Failure handling**: If one sub-scope fails, sibling scopes continue. The failed scope routes through `/PACT:imPACT` independently.

### Step 8: Observe CONSOLIDATE Phase

**What happens**: After all sub-scopes complete, CONSOLIDATE runs:
1. Merges each sub-scope's suffix branch back to the feature branch
2. Cleans up sub-scope worktrees via `/PACT:worktree-cleanup`
3. Dispatches two agents in parallel:
   - `pact-architect`: Verifies cross-scope contract compatibility
   - `pact-test-engineer`: Runs cross-scope integration tests

**Expected outcome**:
- All 3 suffix branches merged to `feature/user-profile`
- All 3 sub-scope worktrees cleaned up
- Architect confirms contract compatibility (exports match imports)
- Test engineer confirms integration tests pass

**Verification**:
```bash
# Sub-scope branches merged
git log --oneline -10  # Should show commits from all 3 scopes on feature branch

# Sub-scope worktrees removed
git worktree list  # Should show only main + feature worktree (not sub-scope worktrees)
```

### Step 9: Observe TEST Phase

**What happens**: After CONSOLIDATE, comprehensive testing runs on the merged feature branch.

**Expected outcome**:
- `pact-test-engineer` dispatched with full feature context
- Integration tests, edge cases, and cross-scope behavior tested
- Test signal emitted (GREEN/YELLOW/RED)

### Step 10: Observe Completion

**Expected outcome**:
- All tasks completed
- Orchestrator prompts for PR creation

## Verification Checks

| Check | How to Verify | Pass Criteria |
|-------|---------------|---------------|
| Scope detection score | Orchestrator output | Score >= 3, lists firing signals |
| S5 framing | Orchestrator presents options | 3 options (decompose, single, adjust) with trade-offs |
| Scope contracts generated | Orchestrator output during ATOMIZE | One contract per sub-scope with owned files and interfaces |
| Sub-scope worktrees created | `git worktree list` during ATOMIZE | One worktree per sub-scope at `.worktrees/{feature}--{scope}` |
| rePACT cycles complete | Agent HANDOFFs returned | All sub-scopes return contract fulfillment |
| ARCHITECT and CODE skipped | Task metadata | Both marked `completed` with `decomposition_active` skip reason |
| Sub-scope branches merged | `git log` on feature branch | Commits from all scopes present |
| Sub-scope worktrees cleaned | `git worktree list` after CONSOLIDATE | Sub-scope worktrees removed |
| Cross-scope compatibility | Architect handoff | No interface mismatches or undelivered contracts |
| Integration tests pass | Test engineer handoff | GREEN signal on cross-scope tests |

## Failure Modes

| Failure | Symptom | Diagnosis |
|---------|---------|-----------|
| Scope detection does not fire | "Single scope" output for multi-domain task | Check counter-signals (shared data models, small scope) reducing the score below threshold. Verify scoring math. |
| Counter-signals incorrectly suppress | Score below threshold despite clear domain separation | Counter-signal for "shared data models" may fire incorrectly if domains share a types file. Review PREPARE output for accuracy. |
| Autonomous tier fires unexpectedly | Decomposition without user confirmation | Both strong signals fired, no counter-signals, and `autonomous-scope-detection: enabled` is in CLAUDE.md. Remove the config to require confirmation. |
| Sub-scope conflict on shared files | Merge conflict during CONSOLIDATE | Scope contracts did not properly assign file ownership. Check `shared_files` constraints in contracts. |
| rePACT nesting violation | Error about nesting depth | rePACT sub-scopes cannot themselves trigger scope detection (bypass rule). Max nesting is 1 level. |
| CWD invalidation during cleanup | Bash commands fail after worktree removal | CONSOLIDATE cleanup must navigate to repo root before removing each sub-scope worktree. Same CWD issue as worktree lifecycle scenario. |
| HALT during sub-scope | All work stops, partial sub-scopes | Expected behavior per algedonic protocol: parent orchestrator stops ALL sub-scopes on HALT. Preserve work-in-progress. After resolution, review interrupted scopes. |
| Single sub-scope detected | Detection fires but only finds 1 scope | Single sub-scope guard should fall back to single scope. Decomposition with 1 scope adds overhead with no benefit. |
