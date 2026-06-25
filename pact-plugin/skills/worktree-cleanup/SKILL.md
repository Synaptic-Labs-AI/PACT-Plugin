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

### Step 1.5: Harvest docs/ Artifacts Before Teardown

`git worktree remove` deletes the worktree's `docs/` directory **irrecoverably** — that directory is gitignored and worktree-ephemeral, so the phase artifacts it holds (`docs/preparation/`, `docs/architecture/`, `docs/plans/`, `docs/review/`, `docs/decision-logs/`) are gone the instant the worktree is removed. Those artifacts are the **fuller substance** behind each phase's distilled HANDOFF. This step is the single chokepoint that protects them for **all** teardown callers — `peer-review` auto-cleanup, CONSOLIDATE sub-scope early-teardown, and manual `/PACT:worktree-cleanup` alike — so harvest MUST precede remove.

Glob the target worktree's `docs/` for artifacts, then apply this conditional guard:

- **Artifacts exist AND a secretary/team is reachable** (the normal workflow-driven teardown): trigger a secretary harvest of the worktree's `docs/` artifacts and **confirm it completes before** proceeding to Step 2. The secretary reads + distills each artifact into pact-memory (its `pact-handoff-harvest` Step 3.5 resolves `artifact_paths` events and reads the disk artifacts while the worktree is still live — this guard is what guarantees that liveness). Do NOT remove the worktree until the harvest is confirmed done.
- **Artifacts exist but no secretary/team is reachable** (e.g. a manual cleanup in a fresh session with no active team): do NOT silently delete. Surface a **loud warning** that the worktree's `docs/` artifacts have NOT been harvested and will be **irrecoverably deleted** by removal, and let the user decide whether to proceed, harvest manually first, or abort.
- **No `docs/` artifacts in the worktree**: nothing to protect — proceed directly to Step 2.

This guard is conditional by design: it must NOT unconditionally block, or it would break the no-team manual-cleanup path. (A user who chooses `--force` past the loud warning is the accepted out-of-scope edge.)

### Step 2: Navigate to Repo Root and Remove the Worktree

Before removal, the shell's working directory must NOT be inside the worktree being removed. Compute the repo root, `cd` to it, and remove the worktree — all in a single bash call. This is critical because if the shell CWD is inside the deleted worktree, all subsequent commands will fail.

```bash
# Compute repo root, cd there, then remove the worktree — must be ONE bash call
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" && pwd)
cd "$REPO_ROOT" && git worktree remove "$REPO_ROOT/.worktrees/{branch}"
```

Note: Claude Code's Bash tool persists the working directory between calls. After this command, subsequent calls will run from `$REPO_ROOT`.

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

### Step 3: Delete the Branch

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

### Step 4: Report

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

