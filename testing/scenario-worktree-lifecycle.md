# Integration Test Scenario: Worktree Lifecycle

**Priority**: P0
**Type**: Integration (runbook)
**Estimated Time**: 15-25 minutes

## Scenario Description

Verify the complete worktree lifecycle: setup (branch creation, directory isolation, gitignore handling), work execution within the worktree, and cleanup (worktree removal, branch deletion). Covers both the happy path and common edge cases (reuse, uncommitted changes, stale worktrees).

## Prerequisites

1. PACT plugin installed with worktree skills (`pact-plugin/skills/worktree-setup/SKILL.md`, `pact-plugin/skills/worktree-cleanup/SKILL.md`)
2. A git repository with at least one commit on the main branch
3. No existing worktrees for the test branch name (verify with `git worktree list`)
4. `.worktrees/` directory either does not exist yet or is already in `.gitignore`
5. Sufficient disk space for an additional worktree (full repo copy)

## Steps

### Step 1: Invoke Worktree Setup

```
/PACT:worktree-setup feature/test-worktree
```

Or trigger it via `/PACT:orchestrate` or `/PACT:comPACT`, which invoke worktree setup automatically.

**What happens**: The worktree-setup skill follows its process:
1. Checks for existing worktree (`git worktree list`)
2. Resolves the main repo root via `git rev-parse --git-common-dir`
3. Creates `.worktrees/` directory if needed
4. Ensures `.worktrees/` is in `.gitignore`
5. Creates the worktree with `git worktree add`

**Expected outcome**:
```
Worktree ready at {REPO_ROOT}/.worktrees/feature/test-worktree
Branch: feature/test-worktree
```

### Step 2: Verify Worktree State

**Verification checks**:
```bash
# Worktree appears in list
git worktree list

# Directory exists with full repo contents
ls {REPO_ROOT}/.worktrees/feature/test-worktree/

# Branch exists and is checked out in the worktree
git -C {REPO_ROOT}/.worktrees/feature/test-worktree branch --show-current

# .worktrees/ is gitignored
grep '.worktrees' {REPO_ROOT}/.gitignore
```

**Expected**:
- `git worktree list` shows both the main directory and `.worktrees/feature/test-worktree`
- The worktree directory contains the full repository file tree
- The branch `feature/test-worktree` is checked out inside the worktree
- `.worktrees/` appears in `.gitignore`

### Step 3: Perform Work in Worktree

Create a file and commit it inside the worktree to simulate agent work.

```bash
cd {REPO_ROOT}/.worktrees/feature/test-worktree
echo "test content" > test-file.txt
git add test-file.txt
git commit -m "test: add test file in worktree"
```

**Expected outcome**: Commit exists on `feature/test-worktree` branch inside the worktree. The main working directory is unaffected.

**Verification**:
```bash
# Commit exists in worktree
git -C {REPO_ROOT}/.worktrees/feature/test-worktree log --oneline -1

# Main directory is unaffected
ls {REPO_ROOT}/test-file.txt  # Should fail (file not found)
```

### Step 4: Test Worktree Reuse

Invoke worktree setup again for the same branch.

```
/PACT:worktree-setup feature/test-worktree
```

**Expected outcome**: The skill detects the existing worktree and reuses it:
```
Reusing existing worktree at {REPO_ROOT}/.worktrees/feature/test-worktree
```

No new worktree is created. The existing worktree (with the test commit) is preserved.

### Step 5: Invoke Worktree Cleanup

```
/PACT:worktree-cleanup feature/test-worktree
```

**What happens**: The worktree-cleanup skill follows its process:
1. Identifies the target worktree
2. Navigates to repo root (critical: CWD must not be inside the worktree being removed)
3. Runs `git worktree remove`
4. Deletes the local branch with `git branch -d`

**Expected outcome**:
```
Cleaned up worktree for feature/test-worktree
  Worktree removed: .worktrees/feature/test-worktree
  Branch deleted: feature/test-worktree
```

### Step 6: Verify Cleanup State

```bash
# Worktree no longer in list
git worktree list

# Directory is gone
ls {REPO_ROOT}/.worktrees/feature/test-worktree  # Should fail

# Branch is deleted
git branch | grep "feature/test-worktree"  # Should return nothing
```

### Step 7: Test Edge Case -- Uncommitted Changes

Create a new worktree and leave uncommitted changes:

```bash
# Setup
git worktree add {REPO_ROOT}/.worktrees/feature/dirty-test -b feature/dirty-test
echo "uncommitted" > {REPO_ROOT}/.worktrees/feature/dirty-test/dirty-file.txt
```

Then attempt cleanup:
```
/PACT:worktree-cleanup feature/dirty-test
```

**Expected outcome**: Git refuses removal. The skill presents options to the user:
```
Cannot remove worktree -- uncommitted changes exist in .worktrees/feature/dirty-test.
Options:
  1. Commit or stash changes first, then retry cleanup
  2. Force removal (discards uncommitted changes permanently)
```

The skill does NOT force-remove automatically. User must choose.

### Step 8: Test Edge Case -- Stale Worktree

Simulate a stale worktree by manually deleting the directory:

```bash
rm -rf {REPO_ROOT}/.worktrees/feature/stale-test
```

Then run:
```bash
git worktree list  # Shows stale entry
git worktree prune  # Cleans up stale refs
git worktree list  # Stale entry removed
```

**Expected outcome**: `git worktree prune` cleans up the stale reference. The worktree-setup skill handles this automatically when it detects a prunable worktree in Step 1 of its process.

## Verification Checks

| Check | How to Verify | Pass Criteria |
|-------|---------------|---------------|
| Worktree created | `git worktree list` | New entry at `.worktrees/{branch}` |
| Branch created | `git branch` | Feature branch exists |
| .worktrees/ gitignored | `grep '.worktrees' .gitignore` | Entry present |
| Filesystem isolation | File created in worktree not visible in main | `ls` in main directory fails for worktree-only files |
| Worktree reuse | Second setup call for same branch | "Reusing existing worktree" message |
| Cleanup removes directory | `ls .worktrees/{branch}` after cleanup | Directory does not exist |
| Cleanup deletes branch | `git branch` after cleanup | Branch not listed |
| CWD safety | Shell continues working after cleanup | Subsequent bash commands succeed (CWD is repo root, not deleted worktree) |
| Uncommitted changes blocked | Cleanup with dirty worktree | User prompted with options, no force removal |
| Stale worktree handled | Prunable entry in worktree list | `git worktree prune` cleans it up |

## Failure Modes

| Failure | Symptom | Diagnosis |
|---------|---------|-----------|
| CWD inside deleted worktree | All subsequent bash commands fail with "No such file or directory" | The cleanup skill did not navigate to repo root before removing. This is the most critical failure mode. The worktree-cleanup SKILL.md explicitly addresses this: compute repo root, cd, and remove in a single bash call. |
| .worktrees/ committed to git | `.worktrees/` directory appears in `git status` | `.gitignore` entry missing or added after files were tracked |
| Branch not deleted | Branch persists after worktree removal | `git branch -d` failed because branch is not merged; user must choose force delete |
| Worktree create fails | "fatal: '{branch}' is already checked out" | Branch exists in another worktree; the setup skill should detect this via `git worktree list` |
| Plan files lost on cleanup | `docs/plans/` content created in worktree was not committed | Key lesson: always commit plan files before removing worktrees. Plan-mode creates files that are not auto-committed. |
