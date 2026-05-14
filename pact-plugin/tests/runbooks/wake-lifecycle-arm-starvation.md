# Counter-test-by-revert runbook: wake-lifecycle Arm starvation fix

> **Why this runbook exists** — per the pinned constraint "Hooks cannot be
> smoke-tested against the running plugin in-session" (`CLAUDE.md`), every
> wake-lifecycle hook fix requires CI + counter-test-by-revert verification.
> The running session uses the OLD hook code at the moment of the fix; in-
> session observability is structurally limited. Counter-test-by-revert
> proves the new tests would FAIL on the unfixed source — falsifying the
> tests-pass-because-the-bug-was-never-real failure mode.

## SOURCE-ONLY revert procedure

The fix is a bundled commit (production source + new tests + runbook +
fixture). Counter-test-by-revert uses a SOURCE-ONLY revert via `cp` and
`git checkout` — `git revert -n` would re-revert the new tests too and
mask the protection cardinality.

```sh
cd <repo-root>
WORKTREE=$(pwd)

# Snapshot current (fix) source.
cp pact-plugin/hooks/wake_lifecycle_emitter.py /tmp/emitter.bak
cp pact-plugin/hooks/shared/wake_lifecycle.py /tmp/shared.bak
cp pact-plugin/hooks/hooks.json /tmp/hooks.bak

# Revert source-only — drain hook is a NEW file, so move it aside.
git checkout HEAD~1 -- pact-plugin/hooks/wake_lifecycle_emitter.py \
                       pact-plugin/hooks/shared/wake_lifecycle.py \
                       pact-plugin/hooks/hooks.json
mv pact-plugin/hooks/wake_inbox_drain.py /tmp/drain.bak

# Run the affected test scope and record cardinality.
cd pact-plugin && /Users/mj/.pyenv/versions/3.12.7/bin/python -m pytest \
    tests/test_wake_lifecycle_arm_starvation.py \
    tests/test_wake_inbox_drain.py \
    -v 2>&1 | tail -40
cd "$WORKTREE"
```

## Expected cardinality on revert

Targets pending test-engineer's empirical measurement against the merge-
parent. Architect targets:

- **`tests/test_wake_lifecycle_arm_starvation.py`** (10 tests):
  - Tests 1, 2, 3, 8, 9 FAIL — the teammate-Arm pre-branch is gone, so no
    marker is written; tests asserting marker presence or filename schema
    raise AssertionError; the O_EXCL collision test fails because
    `_maybe_write_teammate_arm_marker` is undefined.
  - Tests 4, 5, 6, 7 PASS — these exercise the pre-existing lead-session
    branches (Arm on TaskCreate, re-Arm on TaskUpdate(in_progress),
    Teardown on terminal-status, teammate-suppression on terminal-
    status). They survive the revert because the branches below the
    lead-session early-return are unchanged.
  - Test 10 PASSES — the `_ARM_DIRECTIVE` literal is in the unchanged
    pre-revert source.
- **`tests/test_wake_inbox_drain.py`** (6 tests):
  - Full collection error — the module file is missing. All 6 tests
    fail at collection time.

Total on this scope's revert: ~5 test failures + 1 collection error in
the arm_starvation file + 1 collection error in the drain file. (4
arm_starvation tests survive; 6 drain tests fail at collection.)

## Restore

```sh
cp /tmp/emitter.bak pact-plugin/hooks/wake_lifecycle_emitter.py
cp /tmp/shared.bak pact-plugin/hooks/shared/wake_lifecycle.py
cp /tmp/hooks.bak pact-plugin/hooks/hooks.json
cp /tmp/drain.bak pact-plugin/hooks/wake_inbox_drain.py

git diff --quiet -- pact-plugin/hooks/wake_lifecycle_emitter.py \
                    pact-plugin/hooks/shared/wake_lifecycle.py \
                    pact-plugin/hooks/hooks.json \
                    pact-plugin/hooks/wake_inbox_drain.py
echo "exit code: $?"  # MUST be 0 — clean restore.
git status --porcelain -- pact-plugin/hooks/wake_lifecycle_emitter.py \
                          pact-plugin/hooks/shared/wake_lifecycle.py \
                          pact-plugin/hooks/hooks.json \
                          pact-plugin/hooks/wake_inbox_drain.py
# MUST print nothing.
```

## Post-merge fresh-session validation

In a NEW Claude Code session (so the platform reloads the merged hook
code), confirm the live behavior covers both dual-starvation surfaces.

### V1 — Teammate self-claim surface (issue body acceptance criterion)

```
Setup: Start a fresh session in a PACT-Plugin worktree. Trigger
       /PACT:orchestrate or /PACT:comPACT with any non-trivial task that
       dispatches a teammate (e.g. backend-coder, architect).

Sequence:
  1. Lead dispatches the teammate via Agent + TaskCreate.
  2. Teammate session starts; per pact-agent-teams §On Start, teammate
     calls TaskUpdate(taskId, status="in_progress").
  3. Within ONE lead-prompt cycle of the teammate self-claim:
     - Lead receives _ARM_DIRECTIVE additionalContext in next prompt.
     - Lead invokes Skill("PACT:start-pending-scan").
     - CronList shows a fresh /PACT:scan-pending-tasks entry.

Pass criteria:
  - CronList output contains a line with suffix ": /PACT:scan-pending-tasks"
    within ≤ 2 lead prompts of the teammate self-claim transition.
  - Inspect ~/.claude/teams/{team}/wake_inbox/ — expect empty (markers
    were drained on the lead's prompt).

Failure mode (Arm starvation bug back):
  - No CronList entry; wake_inbox/ contains stale markers; lead must
    /PACT:start-pending-scan manually.
```

### V2 — Lead-side unowned-create-then-owner-update surface (B-1 fallback)

```
Setup: Same as V1, but observe the lead's dispatch pattern carefully.
       The lead's /PACT:orchestrate flow creates tasks with TaskCreate
       (initially unowned), then TaskUpdate(owner=teammate) to assign
       — NO status transition. Empirically (per architect §1 / current
       session pact-fb3423e5), this surface ALSO fails without the fix:
       the TaskCreate sees count=0 (unowned excluded); the subsequent
       TaskUpdate(owner=...) carries no status change → no Arm trigger.

Sequence:
  1. Lead's next prompt after the dispatch fires
     wake_inbox_drain.py UserPromptSubmit hook.
  2. Drain inbox: typically empty for this surface (teammate-side write
     happens only on status=in_progress claim).
  3. B-1 fallback: count_active_tasks(team) >= 1 because the task on
     disk is teammate-owned. Emit Arm.

Pass criteria:
  - Lead's first prompt after dispatch carries the Arm directive
    additionalContext.
  - CronList shows a fresh /PACT:scan-pending-tasks entry.

Failure mode: B-1 fallback predicate broken; lead never re-arms scan
on unowned-create-then-owner-update.
```

## Verification matrix

| Test scope | Pass criteria | Counter-test cardinality on revert |
|-----------|----------------|-------------------------------------|
| `test_wake_lifecycle_arm_starvation.py` (10 tests) | All pass on fix | ~5 fail, 5 pass on revert (tests 1, 2, 3, 8, 9 fail; tests 4–7 + 10 pass — lead-session paths and audit-anchor literal survive) |
| `test_wake_inbox_drain.py` (6 tests) | All pass on fix | Collection error on revert (module file missing); all 6 tests fail at collection |
| Existing `test_wake_lifecycle_bug_*.py` (full) | All pass on fix; no regressions | N/A — orthogonal to this PR's surface |

## Known limitations

1. **Smoke-test of the running session is impossible.** Per CLAUDE.md
   pinned context: the running session uses the OLD hook code. V1 and
   V2 validation must run in a fresh post-merge session.

2. **#738 prose bug is co-resident, not co-fixed.** The architecture
   spec §6 resolved the apparent #738 contradiction by source walk
   (`_lifecycle_relevant` step 4 correctly excludes lead-owned tasks).
   The "First active teammate task created" prose is provably-false on
   re-fires; that's a separate prose-bug ticket and does NOT block #754.

3. **Counter-test cardinality is a target, not a measurement.** Test-
   engineer empirically records the actual revert cardinality in the
   PR before merge.
