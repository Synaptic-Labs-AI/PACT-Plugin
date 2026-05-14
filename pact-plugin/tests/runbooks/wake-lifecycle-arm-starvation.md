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
# Anchor MUST resolve to the pre-fix commit. The branch carries the
# bundled fix commit PLUS follow-on version-bump and test-additions
# commits, so HEAD~N varies with branch state. Use `git merge-base`
# against origin/main to resolve the pre-fix anchor independent of
# subsequent commit count.
PRE_FIX=$(git merge-base HEAD origin/main)
git checkout "$PRE_FIX" -- pact-plugin/hooks/wake_lifecycle_emitter.py \
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

### Architect §8.4 targets

- `test_wake_lifecycle_arm_starvation.py`: tests 1, 2, 3, 8, 9 fail;
  tests 4–7 + 10 pass. ~5 fail + 5 pass.
- `test_wake_inbox_drain.py`: full collection error; all 6 fail.

### Empirical (test-engineer, 2026-05-14, revert against `git merge-base HEAD origin/main`)

Measured by `cp`-snapshot + `git checkout "$PRE_FIX" -- <source files>`
(where `PRE_FIX=$(git merge-base HEAD origin/main)`) + `rm
wake_inbox_drain.py`, then re-running the full new-test scope.

**Total: 14 failed + 7 passed.**

- **`test_wake_lifecycle_arm_starvation.py` (10 tests): 3 fail + 7 pass.**
  - FAIL: test 1 (`test_teammate_self_claim_writes_inbox_marker`),
    test 8 (`test_marker_filename_schema`),
    test 9 (`test_marker_o_excl_collision_silent`).
  - PASS: tests 2, 3, 4, 5, 6, 7, 10.
  - Architect target said tests 2 and 3 would fail on revert.
    Empirically they pass: tests 2 (lead-owner negative) and 3
    (metadata-only negative) assert NO marker is written, which is
    also true on the reverted (no-marker-writer-at-all) source.
    This is documented PHANTOM-GREEN on revert — the tests are
    correctly coupled to the negative-case contract under the fix
    but pass trivially on revert. They retain value as future-
    regression guards (a hypothetical buggy implementation that
    accidentally wrote markers for the lead-owner or metadata-only
    cases would be caught). Same shape for test 7 (teammate-side
    Teardown still suppressed) — pre-existing #737 symmetric guard
    already suppresses on revert.
- **`test_wake_inbox_drain.py` (6 tests): 6 fail (not 1 collection error).**
  - All 6 subprocess fires return RC=2 with stderr "can't open file …
    wake_inbox_drain.py: No such file or directory". The test
    harness uses subprocess invocation rather than module import,
    so the missing module surfaces as runtime subprocess-error
    AssertionError (`assert rc == 0`) rather than pytest collection
    error. Functionally equivalent — all 6 tests are coupled to the
    fix.
- **`test_wake_lifecycle_arm_edge_cases.py` (5 tests): 5 fail.**
  - Test-engineer additions covering: drain hook fail-open on missing
    team config; emitter path-traversal rejection (clause 1); empty
    task_id rejection (clause 6); empty session_id rejection;
    separator-bearing id sanitization. All 5 fail on revert because
    `_maybe_write_teammate_arm_marker` is undefined and the drain
    hook file is missing.

### Interpretation

The fix's load-bearing protection is the 14-test surface that fails on
revert. The 7 phantom-green tests (2, 3, 4, 5, 6, 7, 10) split into:

- Tests 4, 5, 6 — lead-session regression guards. Correctly pass on
  revert (the lead-session branches below the guard are unchanged);
  their value is forward-protection against any future Arm-path
  refactor that breaks lead-session emit.
- Tests 2, 3, 7 — teammate-side negative cases. Phantom-green on
  revert (the entire teammate-side write path is gone). They will
  catch future regressions where a buggy implementation accidentally
  writes markers in these negative cases.
- Test 10 — audit-anchor literal-prose pin. Passes on revert because
  the `_ARM_DIRECTIVE` literal exists in the pre-fix emitter source
  (the lift was a refactor; the literal itself was always present).

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

| Test scope | Pass criteria | Counter-test cardinality on revert (empirical 2026-05-14) |
|-----------|----------------|-------------------------------------|
| `test_wake_lifecycle_arm_starvation.py` (10 tests) | All pass on fix | 3 fail (1, 8, 9), 7 pass (2, 3, 4, 5, 6, 7, 10) — negative-case tests phantom-green on revert (see Interpretation above) |
| `test_wake_inbox_drain.py` (6 tests) | All pass on fix | 6 fail via subprocess RC=2 "No such file" (test harness uses subprocess, not import, so this surfaces as test failure rather than collection error) |
| `test_wake_lifecycle_arm_edge_cases.py` (5 tests) | All pass on fix | 5 fail — drain fail-open, path-traversal, empty task_id, empty session_id, separator sanitization all fail on revert |
| Existing `test_wake_lifecycle_bug_*.py` (full) | All pass on fix; no regressions | N/A — orthogonal to this PR's surface |
| Full suite (`pytest tests/`) | 7669 passed, 10 skipped post-fix (7664 baseline + 5 edge-case additions) | — |

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
