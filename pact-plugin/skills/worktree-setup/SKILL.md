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

- If a worktree for the target branch already exists, **reuse it**. Report: "Reusing existing worktree at {path}" and skip to Step 6.
- If the branch exists but has no worktree, ask the user: "Branch `{branch}` already exists. Check out existing branch, or create a new branch name?"

### Step 2: Ensure `.worktrees/` Directory

All worktrees live in `.worktrees/` relative to the repo root.

Use `--git-common-dir` instead of `--show-toplevel` because the latter returns the worktree root when run inside a worktree (e.g., when ATOMIZE creates sub-scope worktrees from the feature worktree).

```bash
# Get main repo root (works correctly even from inside a worktree)
MAIN_GIT_DIR=$(git rev-parse --git-common-dir)
REPO_ROOT=$(dirname "$MAIN_GIT_DIR")

# Create directory if needed
mkdir -p "$REPO_ROOT/.worktrees"
```

### Step 3: Ensure `.worktrees/` Is Gitignored

The `.worktrees/` directory must not be tracked by git.

```bash
# Check if .worktrees is already in .gitignore
grep -q '^\.worktrees' "$REPO_ROOT/.gitignore" 2>/dev/null
```

If `.worktrees` is NOT in `.gitignore`:
1. Append `.worktrees/` to `.gitignore`
2. Commit the change: `git add .gitignore && git commit -m "chore: add .worktrees/ to .gitignore"`

### Step 4: Create the Worktree

```bash
git worktree add "$REPO_ROOT/.worktrees/{branch}" -b {branch}
```

Where `{branch}` is the feature branch name (e.g., `feature-auth` or `feature-auth--backend` for sub-scopes).

**If creation fails**:
- Branch already exists: Ask user whether to check out the existing branch (`git worktree add "$REPO_ROOT/.worktrees/{branch}" {branch}` without `-b`)
- Disk/permissions error: Surface git's error message and offer fallback to working in the main repo directory

### Step 5: Install Dependencies

Auto-detect the project's dependency system and install in the new worktree.

```bash
cd "$REPO_ROOT/.worktrees/{branch}"
```

Check for and run the **first matching** dependency installer:

| File Present | Command |
|-------------|---------|
| `package.json` | `npm install` |
| `Cargo.toml` | `cargo build` |
| `requirements.txt` | `pip install -r requirements.txt` (ensure the project's virtual environment is activated first) |
| `pyproject.toml` | `poetry install` |
| `go.mod` | `go mod download` |

If none of these files exist, skip this step (no dependencies to install).

### Step 6: Verify Clean Baseline

Run the project's test suite to ensure the worktree starts from a clean state.

```bash
cd "$REPO_ROOT/.worktrees/{branch}"
```

Use the project's standard test command (e.g., `npm test`, `cargo test`, `pytest`, `go test ./...`). Choose based on the same dependency file detected in Step 5.

**If tests pass**: Continue to Step 7.

**If tests fail**: Report the failures to the user and ask: "Tests are failing in the new worktree. This likely means tests also fail on the base branch. Proceed anyway, or investigate first?"

### Step 7: Report

Output the result:

```
Worktree ready at {REPO_ROOT}/.worktrees/{branch}
Branch: {branch}
Tests: passing (or: failing — user chose to proceed)
```

**Return the worktree path** so it can be passed to subsequent phases and agents.

## Output

The worktree absolute path. This path is used by:
- The orchestrator to direct agents to the correct working directory
- ATOMIZE to track sub-scope worktree locations
- Subsequent phases operating within this worktree

## Edge Cases

| Case | Handling |
|------|---------|
| Already in a worktree for this feature | Detect via `git worktree list`, reuse existing |
| Worktree directory exists but is stale | Run `git worktree prune` first, then retry |
| Branch name already exists | Ask user: check out existing or create new name |
| Creation fails (disk/permissions) | Surface error, offer fallback to main repo |
| No dependency files found | Skip dependency install step |
| Tests fail on fresh worktree | Report and ask user whether to proceed |
