---
name: worktree-cleanup
description: |
  Remove a git worktree and its branch after PACT workflow completes.
  Use when: PR is merged, sub-scope work is consolidated, or manual cleanup is needed.
  Triggers on: worktree cleanup, remove worktree, delete worktree, branch cleanup.
user_invokable: true
---

# Worktree Cleanup

Remove a git worktree and its associated branch after work is complete. Typically invoked after a PR is merged, after CONSOLIDATE merges sub-scope branches, or manually by the user.

## When to Use

- After `peer-review` merges a PR (automatic cleanup)
- After CONSOLIDATE merges sub-scope branches
- Manual cleanup of stale worktrees (`/PACT:worktree-cleanup`)
- User aborts a workflow and wants to clean up

## Process

Follow these steps in order. Surface all git errors clearly — the user resolves them.

### Step 1: Identify Target

Determine which worktree to remove.

**If a worktree path or branch name was provided**: Use that directly.

**If no target was specified**: List all worktrees and ask the user which to clean up.

```bash
git worktree list
```

Present the list and ask: "Which worktree should I remove?"

### Step 2: Navigate Out of the Worktree

Before removal, ensure the current working directory is NOT inside the worktree being removed. Switch to the main repo root.

```bash
# From a worktree, returns absolute path; from main repo, returns relative .git — the cd && pwd wrapper normalizes both to absolute
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" && pwd)
```

Ensure all subsequent commands use absolute paths via `$REPO_ROOT`. Claude Code's shell does not persist `cd` between calls, so do not rely on changing directories.

### Step 3: Remove the Worktree

```bash
git worktree remove "$REPO_ROOT/.worktrees/{branch}"
```

**If removal fails** (uncommitted changes):

Git will refuse with an error like: `fatal: cannot remove: '.worktrees/{branch}' has changes`.

Surface this to the user:
```
Cannot remove worktree — uncommitted changes exist in .worktrees/{branch}.
Options:
  1. Commit or stash changes first, then retry cleanup
  2. Force removal: git worktree remove --force "$REPO_ROOT/.worktrees/{branch}"
     (This discards uncommitted changes permanently)
```

Do NOT force-remove automatically. The user must choose.

### Step 4: Delete the Branch

After the worktree is removed, delete the local branch.

```bash
git branch -d {branch}
```

**If deletion fails** (branch not fully merged):

Git will refuse with an error like: `error: branch '{branch}' is not fully merged`.

Surface this to the user:
```
Cannot delete branch — '{branch}' is not fully merged.
Options:
  1. Merge the branch first, then retry cleanup
  2. Force delete: git branch -D {branch}
     (This deletes the branch even if unmerged — changes may be lost)
```

Do NOT force-delete automatically. The user must choose.

### Step 5: Report

```
Cleaned up worktree for {branch}
  Worktree removed: .worktrees/{branch}
  Branch deleted: {branch}
```

## Edge Cases

| Case | Handling |
|------|---------|
| Worktree has uncommitted changes | Surface git error, offer commit/stash or force options |
| Branch not fully merged | Surface git error, offer merge or force-delete options |
| Worktree directory already gone | Run `git worktree prune` to clean up stale refs, then delete branch |
| Currently inside the target worktree | Navigate to main repo root before removal |
| No worktrees exist | Report "No worktrees found" |
| Multiple worktrees for related branches | List all, let user choose which to remove |

