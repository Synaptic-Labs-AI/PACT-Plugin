---
name: worktree-setup
description: |
  Create an isolated git worktree with a feature branch for PACT workflows.
  Use when: starting a new feature, beginning orchestrate or comPACT,
  creating sub-scope isolation for ATOMIZE, or manually isolating work.
  Triggers on: worktree setup, feature isolation, parallel workflow, new worktree.
user_invokable: true
---

# Worktree Setup

Create an isolated git worktree with a feature branch for PACT workflows. This provides filesystem isolation so multiple features or sub-scopes can run in parallel without interference.

## When to Use

- Starting a new feature workflow (`/PACT:orchestrate`, `/PACT:comPACT`)
- ATOMIZE phase creating sub-scope isolation
- Manually isolating work for a feature branch

## Process

Follow these steps in order. Stop and report any errors to the user.

### Step 1: Check for Existing Worktree

Before creating anything, check if a worktree already exists for this branch.

```bash
git worktree list
```

- If a worktree for the target branch already exists, **reuse it**. Report: "Reusing existing worktree at {path}" and skip to Step 4.
  - If the worktree appears in the list but is marked **prunable**, run `git worktree prune` first and proceed to create a new one.
- If the branch exists but has no worktree, ask the user: "Branch `{branch}` already exists. Check out existing branch, or create a new branch name?"

### Step 2: Ensure `.worktrees/` Directory

All worktrees live in `.worktrees/` relative to the repo root.

```bash
# Get main repo root (from a worktree, returns absolute path; from main repo, returns relative .git — the cd && pwd wrapper normalizes both to absolute)
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" && pwd)

# Create directory and ensure gitignored
mkdir -p "$REPO_ROOT/.worktrees"
grep -q '\.worktrees' "$REPO_ROOT/.gitignore" 2>/dev/null || echo '.worktrees/' >> "$REPO_ROOT/.gitignore"
```

### Step 3: Create the Worktree

```bash
git worktree add "$REPO_ROOT/.worktrees/{branch}" -b {branch}
```

Where `{branch}` is the feature branch name (e.g., `feature-auth` or `feature-auth--backend` for sub-scopes).

**If creation fails**:
- Branch already exists: Ask user whether to check out the existing branch (`git worktree add "$REPO_ROOT/.worktrees/{branch}" {branch}` without `-b`)
- Disk/permissions error: Surface git's error message and offer fallback to working in the main repo directory

### Step 4: Report

Output the result:

```
Worktree ready at {REPO_ROOT}/.worktrees/{branch}
Branch: {branch}
```

**Return the worktree path** so it can be passed to subsequent phases and agents.

## Edge Cases

| Case | Handling |
|------|---------|
| Already in a worktree for this feature | Detect via `git worktree list`, reuse existing |
| Worktree directory exists but is stale | Run `git worktree prune` first, then retry |
| Branch name already exists | Ask user: check out existing or create new name |
| Creation fails (disk/permissions) | Surface error, offer fallback to main repo |
