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

### Step 4: Link the Repo venv (if present)

Link the main checkout's `.venv` into the worktree so tools resolve project dependencies from inside it. This step runs for a newly created worktree and for a reused one.

```bash
# Shell variables do not survive between Bash calls, so recompute the paths here.
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" && pwd)
WORKTREE="$REPO_ROOT/.worktrees/{branch}"

if [ ! -d "$REPO_ROOT/.venv" ]; then
  echo "Venv link: skipped (no .venv in the main checkout)"
else
  # Write .venv with no trailing slash so the rule matches the symlink as well as a directory.
  EXCLUDE=$(git -C "$WORKTREE" rev-parse --git-path info/exclude)
  if grep -qxF '.venv' "$EXCLUDE" 2>/dev/null || printf '%s\n' '.venv' >> "$EXCLUDE"; then
    if [ -e "$WORKTREE/.venv" ] || [ -L "$WORKTREE/.venv" ]; then
      echo "Venv link: .venv already exists in the worktree — left untouched"
    elif ln -s "$REPO_ROOT/.venv" "$WORKTREE/.venv"; then
      echo "Venv link: .venv -> $REPO_ROOT/.venv"
    else
      echo "WARNING: could not create the .venv link — pyright will not resolve imports in this worktree"
    fi
    git -C "$WORKTREE" check-ignore -q .venv || echo "WARNING: .venv is NOT ignored in this worktree — git worktree remove will refuse without --force, and git add -A could commit the link"
  else
    echo "WARNING: could not write $EXCLUDE — .venv link skipped so an unignored link cannot be committed"
  fi
fi
```

Include the line Step 4 printed in the Step 5 report, or the warning when the link could not be made.

A failed link is a warning, not an error: this step is a convenience, and failing the whole setup over it would regress platforms where `ln -s` is unavailable. This is the one deliberate exception to the stop-on-error rule at the top of this skill.

### Step 5: Report

Output the result:

```
Worktree ready at {REPO_ROOT}/.worktrees/{branch}
Branch: {branch}
<the line Step 4 printed>
```

**Return the worktree path** so it can be passed to subsequent phases and agents.

## Edge Cases

| Case | Handling |
|------|---------|
| Already in a worktree for this feature | Detect via `git worktree list`, reuse existing |
| Worktree directory exists but is stale | Run `git worktree prune` first, then retry |
| Branch name already exists | Ask user: check out existing or create new name |
| Creation fails (disk/permissions) | Surface error, offer fallback to main repo |
| Main checkout has no `.venv` | Step 4 prints a one-line skip; no link is created and setup continues |
| `.venv` already exists in the worktree (real directory or a foreign link) | Step 4 reports it and leaves it untouched |
| `.venv` is not ignored (exclude write failed, or the link is tracked) | Step 4 prints a loud warning: `git worktree remove` will refuse without `--force`, and `git add -A` could commit the link. Re-run setup to retry the write |
| Platform without symlink support (e.g. Windows) | The link step warns, names the consequence, and setup continues; the worktree is still usable |
