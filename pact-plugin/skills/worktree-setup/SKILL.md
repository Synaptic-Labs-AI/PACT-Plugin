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
# Resolve the main checkout root.
LOCAL_GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
MAIN_GIT_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
if [ "$(cd "$LOCAL_GIT_DIR" 2>/dev/null && pwd -P)" = "$(cd "$MAIN_GIT_DIR" 2>/dev/null && pwd -P)" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
else
  REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" 2>/dev/null && pwd -P)
fi
REPO_TOP=$(git -C "$REPO_ROOT" rev-parse --show-toplevel 2>/dev/null)
REPO_COMMON_PHYS=$(cd "$REPO_ROOT" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
MAIN_COMMON_PHYS=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
[ -n "$REPO_ROOT" ] && [ -n "$REPO_TOP" ] && [ -n "$REPO_COMMON_PHYS" ] && [ "$REPO_COMMON_PHYS" = "$MAIN_COMMON_PHYS" ] && [ "$(cd "$REPO_TOP" 2>/dev/null && pwd -P)" = "$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)" ] || { echo "Cannot determine the main checkout root (computed ${REPO_ROOT:-<none>})."; exit 1; }

# Create directory and ensure gitignored
mkdir -p "$REPO_ROOT/.worktrees"
# Append the slash-free rule only when git does not already ignore the path (".worktrees/" does not match a symlink).
git -C "$REPO_ROOT" check-ignore -q .worktrees 2>/dev/null || {
  if grep -qxF '/.worktrees' "$REPO_ROOT/.gitignore" 2>/dev/null; then
    echo "WARNING: .worktrees is NOT ignored even though .gitignore has a '/.worktrees' line — a later negation rule cancels it, so git add -A can stage the worktree collection"
  else
    # Terminate a final line that lacks a newline, so the entry cannot merge into it.
    if [ -s "$REPO_ROOT/.gitignore" ] && [ -n "$(tail -c 1 "$REPO_ROOT/.gitignore" 2>/dev/null)" ]; then
      printf '\n' >> "$REPO_ROOT/.gitignore"
    fi
    echo '/.worktrees' >> "$REPO_ROOT/.gitignore"
  fi
}
```

### Step 3: Create the Worktree

`{branch}` must be a single line and must contain no `\n` or `\r` at all — a value that contains either is not a branch name, so report it instead of substituting it. Do not substitute a value you have not checked for newlines — the fence cannot stop a value that supplies its own heredoc delimiter line. If the name still contains the literal `{branch}` placeholder, stop and report it.

A `.worktrees` that is itself a symlink is supported: the collection is wherever that link resolves, and the worktree is created at the resolved location. A symlink below the collection root, or a target that resolves outside the resolved root, is refused.

```bash
# Shell variables do not survive between Bash calls, so recompute the paths here.
LOCAL_GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
MAIN_GIT_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
if [ "$(cd "$LOCAL_GIT_DIR" 2>/dev/null && pwd -P)" = "$(cd "$MAIN_GIT_DIR" 2>/dev/null && pwd -P)" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
else
  REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" 2>/dev/null && pwd -P)
fi
REPO_TOP=$(git -C "$REPO_ROOT" rev-parse --show-toplevel 2>/dev/null)
REPO_COMMON_PHYS=$(cd "$REPO_ROOT" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
MAIN_COMMON_PHYS=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
[ -n "$REPO_ROOT" ] && [ -n "$REPO_TOP" ] && [ -n "$REPO_COMMON_PHYS" ] && [ "$REPO_COMMON_PHYS" = "$MAIN_COMMON_PHYS" ] && [ "$(cd "$REPO_TOP" 2>/dev/null && pwd -P)" = "$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)" ] || { echo "Cannot determine the main checkout root (computed ${REPO_ROOT:-<none>})."; exit 1; }

BRANCH=
BRANCH_EXTRA=
{ IFS= read -r BRANCH; IFS= read -r BRANCH_EXTRA || :; } <<'PACT BRANCH'
{branch}
PACT BRANCH
[ -z "$BRANCH_EXTRA" ] || { echo "Cannot create worktree: the branch value must be a single line."; exit 1; }
[ "$BRANCH" != "$(printf '{%s}' branch)" ] || { echo "Cannot create worktree: the branch placeholder was not substituted."; exit 1; }
git -C "$REPO_ROOT" check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || { echo "Cannot create worktree: invalid branch name."; exit 1; }

# A symlinked .worktrees root is supported: containment anchors on its resolved location. Refuse only a symlink below the root, or a target resolving outside it.
mkdir -p "$REPO_ROOT/.worktrees"
TARGET="$REPO_ROOT/.worktrees/$BRANCH"
BASE_PHYS=$(cd "$REPO_ROOT/.worktrees" 2>/dev/null && pwd -P)
[ -n "$BASE_PHYS" ] || { echo "Cannot create worktree: cannot resolve $REPO_ROOT/.worktrees."; exit 1; }
EXISTING="$TARGET"
while [ ! -e "$EXISTING" ] && [ ! -L "$EXISTING" ] && [ "$EXISTING" != "$REPO_ROOT/.worktrees" ]; do
  EXISTING=$(dirname "$EXISTING")
done
EXISTING_PHYS=$(cd "$EXISTING" 2>/dev/null && pwd -P)
[ -n "$EXISTING_PHYS" ] || { echo "Cannot create worktree: $EXISTING cannot be resolved to a directory, so $TARGET cannot be created."; exit 1; }
{ [ "$EXISTING_PHYS" = "$BASE_PHYS" ] || [ "${EXISTING_PHYS#"$BASE_PHYS"/}" != "$EXISTING_PHYS" ]; } || { echo "Cannot create worktree: $TARGET resolves outside .worktrees/."; exit 1; }
git worktree add "$TARGET" -b "$BRANCH"
```

Where `{branch}` is the feature branch name (e.g., `feature-auth` or `feature-auth--backend` for sub-scopes).

**If creation fails**:
- Branch already exists: Ask user whether to check out the existing branch. If yes, run the Step 3 block unchanged except for dropping `-b` (`git worktree add "$TARGET" "$BRANCH"`), keeping the branch read, validation and containment check.
- Disk/permissions error: Surface git's error message and offer fallback to working in the main repo directory

### Step 4: Link the Repo venv (if present)

Link the main checkout's `.venv` into the worktree so tools resolve project dependencies from inside it. This step runs for a newly created worktree and for a reused one. `{branch}` must be a single line and must contain no `\n` or `\r` at all — a value that contains either is not a branch name, so report it instead of substituting it. Do not substitute a value you have not checked for newlines — the fence cannot stop a value that supplies its own heredoc delimiter line. If the name still contains the literal `{branch}` placeholder, stop and report it.

```bash
# Shell variables do not survive between Bash calls, so recompute the paths here.
LOCAL_GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
MAIN_GIT_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
if [ "$(cd "$LOCAL_GIT_DIR" 2>/dev/null && pwd -P)" = "$(cd "$MAIN_GIT_DIR" 2>/dev/null && pwd -P)" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
else
  REPO_ROOT=$(cd "$(dirname "$MAIN_GIT_DIR")" 2>/dev/null && pwd -P)
fi
REPO_TOP=$(git -C "$REPO_ROOT" rev-parse --show-toplevel 2>/dev/null)
REPO_COMMON_PHYS=$(cd "$REPO_ROOT" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
MAIN_COMMON_PHYS=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
[ -n "$REPO_ROOT" ] && [ -n "$REPO_TOP" ] && [ -n "$REPO_COMMON_PHYS" ] && [ "$REPO_COMMON_PHYS" = "$MAIN_COMMON_PHYS" ] && [ "$(cd "$REPO_TOP" 2>/dev/null && pwd -P)" = "$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)" ] || { echo "Venv link: skipped (cannot determine the main checkout root)"; exit 0; }

BRANCH=
BRANCH_EXTRA=
{ IFS= read -r BRANCH; IFS= read -r BRANCH_EXTRA || :; } <<'PACT BRANCH'
{branch}
PACT BRANCH
[ -z "$BRANCH_EXTRA" ] || { echo "Venv link: skipped (the branch value must be a single line)"; exit 0; }
[ "$BRANCH" != "$(printf '{%s}' branch)" ] || { echo "Venv link: skipped (the branch placeholder was not substituted)"; exit 0; }
git -C "$REPO_ROOT" check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || { echo "Venv link: skipped (invalid branch name)"; exit 0; }
WORKTREE="$REPO_ROOT/.worktrees/$BRANCH"

if [ ! -d "$REPO_ROOT/.venv" ]; then
  echo "Venv link: skipped (no .venv at $REPO_ROOT/.venv)"
elif [ ! -f "$WORKTREE/.git" ]; then
  if git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null | grep -Fxq "branch refs/heads/$BRANCH"; then
    echo "Venv link: skipped (a worktree for $BRANCH is registered, but $WORKTREE is not a live worktree; no link is created)"
  else
    echo "Venv link: skipped (no git worktree at $WORKTREE — Step 3 did not create one)"
  fi
else
  BASE_PHYS=$(cd "$REPO_ROOT/.worktrees" 2>/dev/null && pwd -P)
  WORKTREE_PHYS=$(cd "$WORKTREE" 2>/dev/null && pwd -P)
  if [ -z "$BASE_PHYS" ] || [ -z "$WORKTREE_PHYS" ] || [ "${WORKTREE_PHYS#"$BASE_PHYS"/}" = "$WORKTREE_PHYS" ]; then
    echo "Venv link: skipped (worktree path is not under .worktrees/)"
    exit 0
  fi
  WORKTREE_COMMON_DIR=$(cd "$WORKTREE" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null)
  WORKTREE_COMMON_PHYS=
  if [ -n "$WORKTREE_COMMON_DIR" ]; then
    WORKTREE_COMMON_PHYS=$(cd "$WORKTREE" 2>/dev/null && cd "$WORKTREE_COMMON_DIR" 2>/dev/null && pwd -P)
  fi
  if [ -n "$WORKTREE_COMMON_PHYS" ] && [ "$WORKTREE_COMMON_PHYS" != "$MAIN_COMMON_PHYS" ]; then
    echo "Venv link: skipped (the worktree at $WORKTREE belongs to a different repository)"
    exit 0
  fi
  if [ -n "$WORKTREE_COMMON_PHYS" ] && [ "$(git -C "$WORKTREE" symbolic-ref --short -q HEAD 2>/dev/null)" != "$BRANCH" ]; then
    echo "Venv link: skipped (the worktree at $WORKTREE is not on $BRANCH)"
    exit 0
  fi
  # Write /.venv anchored so the rule matches only the root-level entry.
  # Resolve from inside the worktree so a relative result is relative to it.
  EXCLUDE=$(cd "$WORKTREE" 2>/dev/null && git rev-parse --git-path info/exclude 2>/dev/null)
  case "$EXCLUDE" in
    /*) ;;
    '') ;;
    *) EXCLUDE="$WORKTREE_PHYS/$EXCLUDE" ;;
  esac
  IGNORE_FAILED=0
  EXCLUDE_RESOLVED=1
  if [ -z "$EXCLUDE" ]; then
    IGNORE_FAILED=1
    EXCLUDE_RESOLVED=0
  else
    mkdir -p "$(dirname "$EXCLUDE")" 2>/dev/null || IGNORE_FAILED=1
    if [ "$IGNORE_FAILED" = 0 ] && ! grep -qxF '/.venv' "$EXCLUDE" 2>/dev/null; then
      # Terminate a final line that lacks a newline, so the new rule cannot merge into it.
      if [ -s "$EXCLUDE" ] && [ -n "$(tail -c 1 "$EXCLUDE" 2>/dev/null)" ]; then
        printf '\n' 2>/dev/null >> "$EXCLUDE" || IGNORE_FAILED=1
      fi
      if [ "$IGNORE_FAILED" = 0 ]; then
        printf '%s\n' '/.venv' 2>/dev/null >> "$EXCLUDE" || IGNORE_FAILED=1
      fi
    fi
  fi
  if [ "$IGNORE_FAILED" = 0 ]; then
    LINK_CREATED=0
    if [ -e "$WORKTREE/.venv" ] || [ -L "$WORKTREE/.venv" ]; then
      echo "Venv link: .venv already exists in the worktree — left untouched"
    elif ln -s "$REPO_ROOT/.venv" "$WORKTREE/.venv"; then
      LINK_CREATED=1
      echo "Venv link: .venv -> $REPO_ROOT/.venv (read-only, shared with the main checkout — do not install through it)"
    else
      echo "WARNING: could not create the .venv link — tools will not resolve project dependencies in this worktree"
    fi
    if ! git -C "$WORKTREE" check-ignore -q .venv; then
      if [ "$LINK_CREATED" = 1 ]; then
        rm -f "$WORKTREE/.venv"
        echo "WARNING: .venv is NOT ignored in this worktree — the link was removed so git add -A cannot commit it"
      elif [ ! -e "$WORKTREE/.venv" ] && [ ! -L "$WORKTREE/.venv" ]; then
        echo "WARNING: .venv is NOT ignored in this worktree, but no .venv exists there — nothing was created to commit"
      elif git -C "$WORKTREE" ls-files --error-unmatch -- .venv >/dev/null 2>&1; then
        echo "WARNING: .venv is NOT ignored in this worktree because it is tracked — git add -A can commit changes to it; clear it with git rm --cached .venv"
      else
        echo "WARNING: .venv is NOT ignored in this worktree — git add -A could commit it; an untracked unignored path also makes git worktree remove refuse without --force"
      fi
    fi
  else
    CAUSE="could not resolve the exclude path"
    [ "$EXCLUDE_RESOLVED" = 0 ] || CAUSE="could not write $EXCLUDE"
    if [ ! -e "$WORKTREE/.venv" ] && [ ! -L "$WORKTREE/.venv" ]; then
      echo "WARNING: $CAUSE — .venv link skipped so an unignored link cannot be committed"
    elif git -C "$WORKTREE" check-ignore -q .venv 2>/dev/null; then
      echo "WARNING: $CAUSE — $WORKTREE/.venv stays in place and is still ignored by another rule; re-run setup to retry the exclude write"
    else
      echo "WARNING: $CAUSE — $WORKTREE/.venv exists and is NOT ignored; git add -A could commit it, and an untracked unignored path makes git worktree remove refuse without --force"
    fi
  fi
fi
```

Include every line Step 4 printed in the Step 5 report, warnings included.

The `/.venv` rule is written to the clone's common `info/exclude`, so it applies to the main checkout and every linked worktree of the clone, and it stays in place after the worktree or its link is removed.

Treat the link as an advisory read-only, shared view of the main checkout's environment: the filesystem does not enforce it. Environment-managing tools write through it — `pip install`, `uv sync`, and `uv run` (which syncs the environment before running) modify the main checkout's environment, not a worktree-local one. Do not delete it through the worktree path either: `.venv/` with a trailing slash resolves into the shared environment and removes the main checkout's contents. When a branch needs its own dependency set, remove the link with `rm -f .venv` (no trailing slash) and create a local environment in the worktree.

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
| Reused worktree at a non-conventional path | The worktree is still reused; Step 4 prints `Venv link: skipped (a worktree for $BRANCH is registered, but $WORKTREE is not a live worktree; no link is created)`, and no link or rule is written. Pyright import resolution does not apply there, because no link is created |
| No worktree was created or registered for the branch (Step 3 failed or was skipped) | Step 4 prints `Venv link: skipped (no git worktree at $WORKTREE — Step 3 did not create one)` and writes nothing |
| Worktree path outside `.worktrees/` | Step 4 prints `Venv link: skipped (worktree path is not under .worktrees/)`; no link or rule is written |
| A symlink below `.worktrees/`, or a target that resolves outside it | Step 3 refuses before creating anything with `Cannot create worktree: … resolves outside .worktrees/.`; a symlinked target or intermediate directory below the root is not followed. A `.worktrees` ROOT that is itself a symlink is supported — containment anchors on its resolved location and the worktree is created there |
| A dangling symlink or a plain file sits at the target path | Step 3 refuses with `Cannot create worktree: … cannot be resolved to a directory, so … cannot be created.` The path is inside `.worktrees/` but unusable — remove it or choose another branch name |
| `.worktrees/{branch}` holds a worktree of a different repository | Step 4 prints `Venv link: skipped (the worktree at … belongs to a different repository)` and writes nothing; it does not borrow another repository's exclude file or worktree |
| `.worktrees/{branch}` holds a worktree on a different branch, or on a detached HEAD | Step 4 prints `Venv link: skipped (the worktree at … is not on …)`; only the worktree for `{branch}` receives the link |
| Branch name contains `/` | The worktree lives at `.worktrees/{branch}` with intermediate directories. A later setup for the parent segment (`foo` when `foo/bar` exists) fails because `.worktrees/foo` is a non-empty directory — choose another branch name or remove the nested worktree first; cleanup for the parent name refuses through the branch-identity check |
| Invalid branch name, a multi-line value, or an unsubstituted `{branch}` placeholder | Step 3 stops before `git worktree add`; Step 4 prints the corresponding `Venv link: skipped (…)` line and writes nothing |
| Worktree directory exists but is stale | Run `git worktree prune` first, then retry |
| Branch name already exists | Ask user: check out existing or create new name |
| Creation fails (disk/permissions) | Surface error, offer fallback to main repo |
| Main checkout has no `.venv` | Step 4 prints a one-line skip naming the path it checked; no link is created and setup continues |
| `.venv` already exists in the worktree (real directory or a foreign link) | Step 4 reports it and leaves it untouched |
| Exclude write fails with no existing `.venv` | Step 4 prints a warning naming the cause and creates no link, so there is nothing to commit. Re-run setup to retry the write |
| Exclude write fails with a pre-existing `.venv` | The path stays in place. If another rule already ignores it, Step 4 names the cause and says it remains ignored; if not, the unignored-path warning names it, because `git add -A` could commit it and `git worktree remove` refuses without `--force` |
| `.venv` is tracked | `check-ignore` reports a tracked path as not ignored, so Step 4 prints `WARNING: .venv is NOT ignored in this worktree because it is tracked`; `git add -A` could commit changes to it. Clear the entry with `git rm --cached .venv`; re-running setup cannot untrack it |
| An untracked `.venv` path is not ignored | A link created by this run is removed before the warning, so `git add -A` cannot commit it. A pre-existing unignored path stays in place; `git worktree remove` refuses without `--force`. Re-run setup to write a missing rule; if a later `!.venv` rule nullifies it, inspect the exclude file |
| Dependency work in a worktree | The link is advisory read-only and shared: environment-managing tools (`pip install`, `uv sync`, `uv run`) write through it into the main checkout. Remove the link and create a local environment in the worktree instead |
| `git clean -xdf` runs in a worktree | The ignored `.venv` link is removed; pyright reports `venv .venv subdirectory not found in venv path`. Re-run setup to restore the link |
| Main checkout moved or renamed | Every worktree link is dangling. `git status` stays clean in the moved main checkout, but inside a worktree it reports `fatal: not a git repository: <old path>`. Run `git worktree repair <worktree-path>`, remove the dangling link with `rm .venv`, then re-run setup. Re-running Step 4 before the repair prints the unresolved-exclude-path warning and skips the link |
| A linked worktree of a submodule | The main checkout root cannot be determined. The link step prints `Venv link: skipped (cannot determine the main checkout root)` and continues; the create and cleanup steps stop with `Cannot determine the main checkout root (computed …)` and create nothing |
| Platforms where symlinks are unavailable or emulated by copy (e.g. Git Bash/MSYS2) | `ln -s` may copy the environment instead of linking it and still exit 0; do not rely on the worktree sharing the main checkout's packages |
