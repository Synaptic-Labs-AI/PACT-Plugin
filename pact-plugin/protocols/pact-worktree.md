## Worktree Protocol

> **Purpose**: Isolate agent workspaces from orchestrator's view. Worktrees enable parallel
> sub-scopes to work concurrently without file conflicts and keep the main workspace in a
> "known good" state.
>
> **Convention Alignment**: These conventions align with `superpowers:using-git-worktrees`.
> Users of both will see consistent behavior.

---

### Configuration

| Setting | Values | Default | Description |
|---------|--------|---------|-------------|
| `worktree-mode` | `tiered`, `always`, `never` | `tiered` | When to create worktrees |
| `worktree-directory` | path | `.worktrees` | Directory for worktrees |
| `worktree-baseline-tests` | `always`, `on-divergence`, `never` | `always` | When to run baseline tests in new worktrees |

**Mode Behaviors**:

| Mode | orchestrate | rePACT (scoped) | comPACT |
|------|-------------|-----------------|---------|
| `tiered` | Creates worktree | Per-scope worktrees | Optional (on request) |
| `always` | Creates worktree | Per-scope worktrees | Creates worktree |
| `never` | No worktree | No worktrees | No worktree |

---

### Branch Naming Convention

The `--` separator distinguishes worktree branches from intentional sub-branches.

| Workflow | Branch Pattern | Example |
|----------|---------------|---------|
| Standard orchestrate | `feature/{name}--work` | `feature/auth--work` |
| Scoped (per scope) | `feature/{name}--{scope}` | `feature/auth--backend` |
| comPACT (when enabled) | `feature/{name}--compact` | `feature/auth--compact` |

- `{scope}` derives from the scope contract's `scope_id` (kebab-case)
- Worktree path: `{worktree-directory}/{feature-name}--{suffix}`

---

### Worktree Lifecycle

#### Setup Protocol

**Trigger**: After feature branch creation, before agent dispatch.

1. **Verify .gitignore**: `git check-ignore -q .worktrees/`
   - If not ignored, prompt: "`.worktrees/` is not in .gitignore. Add it?"

2. **Create worktree**: `git worktree add .worktrees/{feature}--{suffix} -b feature/{name}--{suffix}`

3. **Run baseline tests** in worktree to verify clean starting state
   - On failure: Report to user, ask whether to proceed or investigate
   - If user aborts: Execute Cleanup Protocol to remove the partially-created worktree

4. **Pass worktree path to agents** in dispatch prompt:
   ```
   GUIDELINES:
   - Working directory: {worktree_path}
   - All file paths relative to this directory
   - Do not access paths outside {worktree_path}
   ```

#### Work Protocol

- Agents commit to worktree branch; main workspace untouched
- Multiple worktrees can run in parallel (scoped orchestration)
- Accessing paths outside assigned worktree is treated as a blocker

#### Merge-Back Protocol

**Trigger**: After all phases complete successfully, before peer-review.

**Single Worktree**: `git merge feature/{name}--work --no-ff -m "Merge worktree work for {name}"`

**Multiple Worktrees (scoped)**: Merge sequentially to detect conflicts early:
1. Merge first sub-scope branch to feature branch
2. For each subsequent sub-scope: attempt merge, stop on conflict
3. After all merges: run integration tests

**Merge Order**: Consider merging smaller or more independent scopes first to reduce conflict likelihood.

**Conflict Handling**: Stop and present options to user:
- A) Resolve manually — pause here
- B) Abort merge, review sub-scope outputs first
- C) Discard this sub-scope's changes, keep previous merges

Orchestrator does not auto-resolve merge conflicts.

#### Cleanup Protocol

**Trigger**: After successful merge to feature branch.

1. Remove worktree: `git worktree remove .worktrees/{feature}--{suffix}`
2. Delete branch: `git branch -d feature/{name}--{suffix}`
   - If branch delete fails (not fully merged due to discarded changes), log warning; user can manually clean orphaned branches with `git branch -D` later.

On cleanup failure: Log warning, continue. Clean up later with `git worktree prune`.

---

### Failure Handling

#### Operational Failure (single sub-scope)

| Scenario | Sibling Sub-scopes | Action |
|----------|-------------------|--------|
| Blocker reported | Continue | Triage via imPACT; siblings may complete |
| Agent stalls | Continue | Apply stall protocol; siblings unaffected |
| imPACT fails | Pause all | Escalate to user |

Operational failures don't invalidate sibling work. Let independent work complete.

#### HALT Signal (viability threat)

1. **Stop all sub-scopes immediately** — no worktree work continues
2. **Do not merge any worktree branches** — preserve isolation
3. **Escalate to user** — await decision before resuming or aborting

A HALT signal indicates viability threats. Prevent cross-contamination until assessed.

#### Abort/Crash Recovery

Worktrees and branches persist after interruption. On resume: check for orphaned worktrees, offer to clean or continue.

```
Orphaned worktrees detected. Run `git worktree prune` to clean up.
```

---

### Integration Points

#### orchestrate.md

1. After feature branch creation: Execute Setup Protocol
2. Before phase dispatch: Pass worktree path to agents
3. After TEST phase: Execute Merge-Back Protocol
4. After successful merge: Execute Cleanup Protocol

#### rePACT.md

1. On scope contract dispatch: Create per-scope worktree
2. In agent prompt: Include worktree path in guidelines
3. On sub-scope completion: Collect for merge-back (don't merge individually)
4. In consolidate phase: Execute sequential Merge-Back Protocol

#### comPACT.md

1. On `worktree-mode: always`: Create worktree before dispatch
2. On heuristic trigger (tiered mode): Propose worktree if task is non-trivial
3. On conversational override: Honor user's inline request

**Heuristic** (tiered mode): Propose worktree when task touches 3+ files across 2+ directories with non-trivial changes. This threshold may be tuned via `worktree-heuristic-threshold` configuration if needed.

---

### Shared Conventions

Aligned with `superpowers:using-git-worktrees` for user consistency:

| Convention | Behavior |
|------------|----------|
| Default directory | `.worktrees/` |
| .gitignore verification | `git check-ignore` before creating |
| Baseline tests | Run and verify before proceeding |
| Project setup | Auto-detect from `package.json`, `Cargo.toml`, etc. |

**Independent implementation**: PACT implements worktree logic independently. The superpowers skill is not required.

**Alignment tracking**: Issue #142 tracks convention alignment with `superpowers:using-git-worktrees` for periodic review. Key areas: directory naming, .gitignore patterns, baseline test behavior.

---
