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

Run this exact command, substituting the target worktree's absolute path from Step 1 — the same path Step 2 will remove:

```bash
[ -d "{abs_worktree}/docs" ] && echo DIR_PRESENT || { [ -x "{abs_worktree}" ] && echo DIR_ABSENT || echo CANNOT_OBSERVE; }
find -L "{abs_worktree}/docs" -type f
```

Then apply this guard. It is **two independent decisions** — what to harvest, and whether removal is licensed. A failed probe must never suppress the harvest; it only withholds the removal.

**1. Harvest whatever `find` listed.** Every listed file is an unharvested artifact, however the probe ended. If a secretary/team is reachable, trigger a secretary harvest of the worktree's `docs/` artifacts and **confirm it completes** before going on. The secretary reads + distills each artifact into pact-memory (its `pact-handoff-harvest` Step 3.5 resolves `artifact_paths` events and reads the disk artifacts while the worktree is still live — this guard is what guarantees that liveness). If `find` listed nothing, there is nothing to harvest.

**2. Removal is licensed only if the probe was conclusive** — `DIR_ABSENT`, or `DIR_PRESENT` with `find` exiting zero — **and** every listed artifact was harvested; then proceed directly to Step 2. Otherwise do NOT remove: surface a **loud warning** naming what is unharvested or unreadable and will be **irrecoverably deleted** by removal, and let the user decide whether to proceed, harvest manually first, or abort. A non-zero exit means `docs/` holds a subtree your instrument could not read, so no harvest can have covered it.

| marker | `find` exit | files listed | harvest | removal |
|---|---|---|---|---|
| `DIR_ABSENT` | non-zero | none | nothing to harvest | licensed |
| `DIR_PRESENT` | zero | none | nothing to harvest | licensed |
| `DIR_PRESENT` | zero | some | harvest them | licensed once harvested |
| `DIR_PRESENT` | non-zero | some | harvest what was listed | **refused** — unread subtree |
| `DIR_PRESENT` | non-zero | none | nothing to harvest | **refused** — unread subtree |
| `CANNOT_OBSERVE` | non-zero | none | nothing to harvest | **refused** — marker untrustworthy |

This guard is conditional by design: it must NOT unconditionally block, or it would break the no-team manual-cleanup path. (A user who chooses `--force` past the loud warning is the accepted out-of-scope edge.)

### Step 2: Navigate to Repo Root and Remove the Worktree

Before removal, the shell's working directory must NOT be inside the worktree being removed. Compute the repo root, `cd` to it, and remove the worktree — all in a single bash call. This is critical because if the shell CWD is inside the deleted worktree, all subsequent commands will fail.

`{branch}` must be a single line; a value containing a newline is not a branch name, so report it instead of substituting it.

```bash
# Compute repo root, cd there, then remove the worktree — must be ONE bash call
LOCAL_GIT_DIR=$(git rev-parse --git-dir)
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
if [ "$(cd "$LOCAL_GIT_DIR" 2>/dev/null && pwd -P)" = "$(cd "$MAIN_GIT_DIR" 2>/dev/null && pwd -P)" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
else
  REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" 2>/dev/null && pwd -P)
fi
REPO_TOP=$(git -C "$REPO_ROOT" rev-parse --show-toplevel 2>/dev/null)
REPO_COMMON_PHYS=$(cd "$REPO_ROOT" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
MAIN_COMMON_PHYS=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
[ -n "$REPO_ROOT" ] && [ -n "$REPO_TOP" ] && [ -n "$REPO_COMMON_PHYS" ] && [ "$REPO_COMMON_PHYS" = "$MAIN_COMMON_PHYS" ] && [ "$(cd "$REPO_TOP" 2>/dev/null && pwd -P)" = "$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)" ] || { echo "Cannot determine the main checkout root (computed ${REPO_ROOT:-<none>})."; exit 1; }

IFS= read -r BRANCH <<'PACT BRANCH'
{branch}
PACT BRANCH
git -C "$REPO_ROOT" check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || { echo "Cannot remove worktree: invalid branch name."; exit 1; }
cd "$REPO_ROOT" && git worktree remove "$REPO_ROOT/.worktrees/$BRANCH"
```

Note: Claude Code's `Bash` tool persists the working directory between calls. After this command, subsequent calls will run from `$REPO_ROOT`.

**If removal fails** (uncommitted changes):

Git will refuse with an error like: `fatal: cannot remove: '.worktrees/{branch}' has changes`.

Surface this to the user:
```
Cannot remove worktree — uncommitted changes exist in .worktrees/{branch}.
Options:
  1. Commit or stash changes first, then retry cleanup
  2. Force removal: repeat the Step 2 block with --force added to git worktree remove
     (This discards uncommitted changes permanently)
```

Do NOT force-remove automatically. The user must choose.

### Step 3: Delete the Branch

After the worktree is removed, delete the local branch.

`{branch}` must be a single line; a value containing a newline is not a branch name, so report it instead of substituting it.

```bash
IFS= read -r BRANCH <<'PACT BRANCH'
{branch}
PACT BRANCH
git check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || { echo "Cannot delete branch: invalid branch name."; exit 1; }
git branch -d "$BRANCH"
```

**If deletion fails** (branch not fully merged):

Git will refuse with an error like: `error: branch '{branch}' is not fully merged`.

Surface this to the user:
```
Cannot delete branch — '{branch}' is not fully merged.
Options:
  1. Merge the branch first, then retry cleanup
  2. Force delete: repeat the Step 3 block with -D instead of -d
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

