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

- If a worktree for the target branch already exists, **reuse it**. Report: "Reusing existing worktree at {path}" and skip to Step 4. Only a worktree at `.worktrees/{branch}` receives the venv link; a reused worktree elsewhere is still reused, just without the link.
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
# Shell variables do not survive between Bash calls, so recompute the paths here.
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" && pwd)

IFS= read -r BRANCH <<'PACT BRANCH'
{branch}
PACT BRANCH
git -C "$REPO_ROOT" check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || { echo "Cannot create worktree: invalid branch name."; exit 1; }
git worktree add "$REPO_ROOT/.worktrees/$BRANCH" -b "$BRANCH"
```

Where `{branch}` is the feature branch name (e.g., `feature-auth` or `feature-auth--backend` for sub-scopes). `{branch}` must be a single line; a value containing a newline is not a branch name, so report it instead of substituting it.

**If creation fails**:
- Branch already exists: Ask user whether to check out the existing branch. If yes, run the Step 3 block unchanged except for dropping `-b` (`git worktree add "$REPO_ROOT/.worktrees/$BRANCH" "$BRANCH"`), keeping the branch read and validation.
- Disk/permissions error: Surface git's error message and offer fallback to working in the main repo directory

### Step 4: Link the Repo venv (if present)

Link the main checkout's `.venv` into the worktree so tools resolve project dependencies from inside it. This step runs for a newly created worktree and for a reused one. `{branch}` must be a single line; a value containing a newline is not a branch name, so report it instead of substituting it.

```bash
# Shell variables do not survive between Bash calls, so recompute the paths here.
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" && pwd)

IFS= read -r BRANCH <<'PACT BRANCH'
{branch}
PACT BRANCH
git -C "$REPO_ROOT" check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || { echo "Venv link: skipped (invalid branch name)"; exit 0; }
WORKTREE="$REPO_ROOT/.worktrees/$BRANCH"

if [ ! -d "$REPO_ROOT/.venv" ]; then
  echo "Venv link: skipped (no .venv at $REPO_ROOT/.venv)"
elif [ ! -f "$WORKTREE/.git" ]; then
  echo "Venv link: skipped (no git worktree at $WORKTREE)"
else
  BASE_PHYS=$(cd "$REPO_ROOT/.worktrees" 2>/dev/null && pwd -P)
  WORKTREE_PHYS=$(cd "$WORKTREE" 2>/dev/null && pwd -P)
  if [ -z "$BASE_PHYS" ] || [ -z "$WORKTREE_PHYS" ] || [ "${WORKTREE_PHYS#"$BASE_PHYS"/}" = "$WORKTREE_PHYS" ]; then
    echo "Venv link: skipped (worktree path is not under .worktrees/)"
    exit 0
  fi
  # Write /.venv anchored so the rule matches only the root-level entry.
  EXCLUDE=$(git -C "$WORKTREE" rev-parse --git-path info/exclude 2>/dev/null)
  IGNORE_FAILED=0
  if [ -z "$EXCLUDE" ]; then
    IGNORE_FAILED=1
  elif ! grep -qxF '/.venv' "$EXCLUDE" 2>/dev/null; then
    # Terminate a final line that lacks a newline, so the new rule cannot merge into it.
    if [ -s "$EXCLUDE" ] && [ -n "$(tail -c 1 "$EXCLUDE" 2>/dev/null)" ]; then
      printf '\n' 2>/dev/null >> "$EXCLUDE" || IGNORE_FAILED=1
    fi
    if [ "$IGNORE_FAILED" = 0 ]; then
      printf '%s\n' '/.venv' 2>/dev/null >> "$EXCLUDE" || IGNORE_FAILED=1
    fi
  fi
  if [ "$IGNORE_FAILED" = 0 ]; then
    LINK_CREATED=0
    if [ -e "$WORKTREE/.venv" ] || [ -L "$WORKTREE/.venv" ]; then
      echo "Venv link: .venv already exists in the worktree — left untouched"
    elif ln -s "$REPO_ROOT/.venv" "$WORKTREE/.venv"; then
      LINK_CREATED=1
      echo "Venv link: .venv -> $REPO_ROOT/.venv"
    else
      echo "WARNING: could not create the .venv link — tools will not resolve project dependencies in this worktree"
    fi
    if ! git -C "$WORKTREE" check-ignore -q .venv; then
      if [ "$LINK_CREATED" = 1 ]; then
        rm -f "$WORKTREE/.venv"
        echo "WARNING: .venv is NOT ignored in this worktree — the link was removed so git add -A cannot commit it"
      else
        echo "WARNING: .venv is NOT ignored in this worktree — git add -A could commit it; an untracked unignored link also makes git worktree remove refuse without --force"
      fi
    fi
  else
    echo "WARNING: could not write $EXCLUDE — .venv link skipped so an unignored link cannot be committed"
  fi
fi
```

Include every line Step 4 printed in the Step 5 report, warnings included.

Treat the link as a read-only, shared view of the main checkout's environment. Do not install into it, and do not delete it through the worktree path: `.venv/` with a trailing slash resolves into the shared environment and removes the main checkout's contents. When a branch needs its own dependency set, remove the link and create a local environment in the worktree.

A failed link is a warning, not an error: this step is a convenience, and failing the whole setup over it would regress platforms where `ln -s` is unavailable. This is the one deliberate exception to the stop-on-error rule at the top of this skill.

### Step 5: Report

Output the result:

```
Worktree ready at {REPO_ROOT}/.worktrees/{branch}
Branch: {branch}
<every line Step 4 printed, warnings included>
```

**Return the worktree path** so it can be passed to subsequent phases and agents.

## Edge Cases

| Case | Handling |
|------|---------|
| Already in a worktree for this feature | Detect via `git worktree list`, reuse existing |
| Reused worktree at a non-conventional path | The worktree is still reused; Step 4 prints `Venv link: skipped (no git worktree at $WORKTREE)`, and no link or rule is written |
| Worktree path outside `.worktrees/` | Step 4 prints `Venv link: skipped (worktree path is not under .worktrees/)`; no link or rule is written |
| Invalid branch name | Step 3 stops with an error; Step 4 prints `Venv link: skipped (invalid branch name)` and writes nothing |
| Worktree directory exists but is stale | Run `git worktree prune` first, then retry |
| Branch name already exists | Ask user: check out existing or create new name |
| Creation fails (disk/permissions) | Surface error, offer fallback to main repo |
| Main checkout has no `.venv` | Step 4 prints a one-line skip naming the path it checked; no link is created and setup continues |
| `.venv` already exists in the worktree (real directory or a foreign link) | Step 4 reports it and leaves it untouched |
| Exclude write fails | Step 4 prints a warning and creates no link, so an unignored link cannot be committed. Re-run setup to retry the write |
| `.venv` is tracked | Step 4 prints a loud warning and `git add -A` could commit changes to it. Clear the entry with `git rm --cached .venv`; re-running setup cannot untrack it |
| An untracked `.venv` link is not ignored | A link created by this run is removed before the warning, so `git add -A` cannot commit it. A pre-existing unignored link stays in place; `git worktree remove` refuses without `--force`. Re-run setup to write a missing rule; if a later `!.venv` rule nullifies it, inspect the exclude file |
| Dependency work in a worktree | The link is read-only and shared. Remove the link and create a local environment in the worktree instead of installing through it |
| `git clean -xdf` runs in a worktree | The ignored `.venv` link is removed; pyright reports `venv .venv subdirectory not found in venv path`. Re-run setup to restore the link |
| Main checkout moved or renamed | Every worktree link is dangling while `git status` stays clean. Remove the link with `rm .venv` and re-run setup after the worktree metadata is repaired |
| Platforms where symlinks are unavailable or emulated by copy (e.g. Git Bash/MSYS2) | `ln -s` may copy the environment instead of linking it and still exit 0; do not rely on the worktree sharing the main checkout's packages |
