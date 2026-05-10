# Post-merge fresh-session validation runbook: wake-lifecycle teachback re-arm

> **Why this runbook exists** — per the pinned constraint "Hooks cannot be smoke-tested
> against the running plugin in-session" (`CLAUDE.md`), every wake-lifecycle hook fix
> requires manual validation in a fresh post-merge session because the running session
> is using the OLD hook code at the moment of the fix. CI tests + counter-test-by-revert
> verify the code; this runbook confirms the live behavior.

## Counter-test-by-revert (CI cardinality verification)

Bundled commit (production fix + new tests in single PR). Use SOURCE-ONLY revert via
`cp` and `git checkout`, NOT `git revert -n` (which would re-revert the new tests too
and mask the protection cardinality).

### Bug A: defer-Teardown branch + has_same_teammate_continuation predicate

```sh
cd <repo-root>
WORKTREE=$(pwd)

# Snapshot current state.
cp pact-plugin/hooks/shared/wake_lifecycle.py /tmp/wake_lifecycle.py.bak
cp pact-plugin/hooks/wake_lifecycle_emitter.py /tmp/wake_lifecycle_emitter.py.bak

# Revert source files only (NOT the tests).
git checkout HEAD~1 -- pact-plugin/hooks/shared/wake_lifecycle.py \
                       pact-plugin/hooks/wake_lifecycle_emitter.py

# Run the affected test scope and record cardinality.
cd pact-plugin && python -m pytest \
    tests/test_has_same_teammate_continuation.py \
    tests/test_wake_lifecycle_bug_a_defer_teardown.py \
    -v 2>&1 | tail -30
cd "$WORKTREE"

# EXPECTED CARDINALITY ON REVERT (empirical, measured against
# 00184b8b^ source):
#   - test_has_same_teammate_continuation.py: ~34 fail, 0 collection
#     errors. The module imports the helper as `import shared.wake_lifecycle
#     as wl` and references `wl.has_same_teammate_continuation` per-test;
#     when the symbol is gone the AttributeError raises per-test rather
#     than at module-collection. Three production-shape regression tests
#     pass on revert (they only exercise the pre-existing _lifecycle_relevant
#     path, not the missing helper).
#   - test_wake_lifecycle_bug_a_defer_teardown.py: 2 fail, 5 pass —
#     test_secretary_bug_a_fixed_via_count_gate_post_empty_carve_out
#     and test_defer_teardown_branch_isolated_at_post_zero. The other 4
#     pass because they were engineered to assert no-Teardown via dual-
#     coverage (count gate covers when defer gate is absent) or to assert
#     positive Teardown emit (negative pairs).
# Total cardinality on this scope's revert: ~36 test failures, 0 collection errors.

# Restore source atomically.
cp /tmp/wake_lifecycle.py.bak pact-plugin/hooks/shared/wake_lifecycle.py
cp /tmp/wake_lifecycle_emitter.py.bak pact-plugin/hooks/wake_lifecycle_emitter.py

# Verify byte-identical restore.
git diff --quiet -- pact-plugin/hooks/shared/wake_lifecycle.py \
                    pact-plugin/hooks/wake_lifecycle_emitter.py
echo "exit code: $?"  # MUST be 0 — clean restore.
git status --porcelain -- pact-plugin/hooks/shared/wake_lifecycle.py \
                          pact-plugin/hooks/wake_lifecycle_emitter.py
# MUST print nothing — clean restore confirmed.
```

### Bug B: re-Arm branch + _is_pending_to_in_progress_transition

Same SOURCE-ONLY revert procedure (the two branches are bundled in the same
commit). Run the Bug B test scope:

```sh
cd pact-plugin && python -m pytest \
    tests/test_wake_lifecycle_bug_b_rearm.py \
    tests/test_is_pending_to_in_progress_transition.py \
    -v 2>&1 | tail -30

# EXPECTED CARDINALITY ON REVERT (empirical, measured against
# 00184b8b^ source):
#   - test_wake_lifecycle_bug_b_rearm.py: 4 fail, 6 pass.
#     test_rearm_on_claim_after_eager_teardown,
#     test_rearm_on_captured_teammate_claim_fixture,
#     test_rearm_on_taskupdate_owned_by_secretary_post_empty_carve_out,
#     and test_sequence_teardown_then_claim_emits_rearm fail (re-Arm
#     branch missing). The 3 audit-anchor tests + 3 negative tests pass.
#   - test_is_pending_to_in_progress_transition.py: 8 fail (entire file).
#     The predicate symbol is gone; every test references it via
#     `emitter._is_pending_to_in_progress_transition` and the
#     AttributeError raises per-test.
# Total cardinality on this scope's revert: ~12 test failures.
```

### Bug C: doc-fix structural pins

```sh
# Snapshot.
cp pact-plugin/protocols/pact-completion-authority.md /tmp/cap.bak
cp pact-plugin/protocols/pact-protocols.md /tmp/protocols.bak
cp pact-plugin/agents/pact-orchestrator.md /tmp/orch.bak
cp pact-plugin/skills/pact-teachback/SKILL.md /tmp/teachback.bak
cp pact-plugin/skills/pact-agent-teams/SKILL.md /tmp/agent_teams.bak

# Revert.
git checkout HEAD~1 -- \
    pact-plugin/protocols/pact-completion-authority.md \
    pact-plugin/protocols/pact-protocols.md \
    pact-plugin/agents/pact-orchestrator.md \
    pact-plugin/skills/pact-teachback/SKILL.md \
    pact-plugin/skills/pact-agent-teams/SKILL.md

# Run.
cd pact-plugin && python -m pytest tests/test_read_trigger_precondition_pinned.py -v
cd "$WORKTREE"

# EXPECTED CARDINALITY ON REVERT (per-test prediction; actual count
# depends on which surfaces the HEAD~1 revert removed the marker from):
#   - test_marker_substring_present_in_doc_surface[*]: up to 5 fail
#     (one parametrized cell per surface that lost the marker).
#   - test_marker_substring_consistent_across_all_surfaces: 1 fail.
#   - test_ssot_anchor_present_in_completion_authority: 1 fail if the
#     SSOT lost the H3 heading.
#   - test_persona_cross_refs_to_ssot_anchor: 1 fail if the persona
#     lost the cross-ref.
#   - test_marker_phrase_count_per_surface[*]: up to 5 fail (one
#     parametrized cell per surface whose marker count drifted).
# Total: up to ~13 fail per full Bug C surface revert.

# Restore.
cp /tmp/cap.bak pact-plugin/protocols/pact-completion-authority.md
cp /tmp/protocols.bak pact-plugin/protocols/pact-protocols.md
cp /tmp/orch.bak pact-plugin/agents/pact-orchestrator.md
cp /tmp/teachback.bak pact-plugin/skills/pact-teachback/SKILL.md
cp /tmp/agent_teams.bak pact-plugin/skills/pact-agent-teams/SKILL.md
git diff --quiet -- pact-plugin/protocols/ pact-plugin/agents/ pact-plugin/skills/
echo "exit code: $?"
```

---

## Manual fresh-session smoke validation (D1-D6)

After PR merge, in a NEW Claude Code session (so the platform reloads the merged
hook code), run:

### D1 — Two-Task non-secretary defer-Teardown

```
Setup: Spawn a non-secretary teammate (e.g., backend-coder) via /PACT:orchestrate.
       The orchestrator dispatches Task A (TEACHBACK) + Task B (work) per the
       canonical Two-Task Dispatch Shape.

Sequence:
  1. Teammate receives Task A, stores teachback metadata, idles on
     awaiting_lead_completion.
  2. Lead inspects metadata, accepts via the two-call atomic pair:
     SendMessage(to=teammate, ...) FIRST, then TaskUpdate(A, status="completed")
     (per CLAUDE.md "Two-call atomic pair: SendMessage FIRST" pin).
  3. Observe: between step 2 (Task A completion) and the teammate's claim of Task B,
     does the inbox-watch Monitor STATE_FILE remain present?

Pass criteria:
  - The Monitor STATE_FILE at ~/.claude/teams/{team}/inbox-wake-state.json
    REMAINS PRESENT throughout Task A completion + Task B claim. No phantom
    1->0 transient.
  - Verify with `ls -la ~/.claude/teams/{team}/inbox-wake-state.json` between
    each step.

Failure mode (Bug A bug back): the STATE_FILE is unlinked between Task A
completion and the next teammate claim → the eager Teardown emitted.
```

### D2 — Bug B re-Arm on teammate claim after STATE_FILE absence

```
Setup: Same two-task dispatch as D1, but manually `rm` the STATE_FILE to simulate
       a Monitor-died-silently scenario:
       rm -f ~/.claude/teams/{team}/inbox-wake-state.json

Sequence:
  1. With STATE_FILE removed, instruct the teammate to claim Task B
     (TaskUpdate(B, status="in_progress")).
  2. Observe: does the lead receive an Arm directive in their next turn?
  3. Verify STATE_FILE is re-armed:
     ls -la ~/.claude/teams/{team}/inbox-wake-state.json

Pass criteria:
  - Lead's next turn carries the Arm directive
    ("First active teammate task created. Invoke Skill(\"PACT:watch-inbox\")...").
  - STATE_FILE is re-created with fresh armed_at timestamp.

Failure mode (Bug B bug back): no Arm directive emitted; STATE_FILE remains
absent; lead never re-arms the Monitor; subsequent SendMessages from teammate
do not wake the lead until next user turn.
```

### D3 — Negative case: single-task dispatch still tears down

```
Setup: Dispatch a SINGLE task (no addBlocks/blocks chain) via TaskCreate.
       e.g., a one-shot research task with no follow-on.

Sequence:
  1. Teammate completes the task (TaskUpdate(status="completed")).
  2. Observe: lead receives Teardown directive in next turn.
  3. STATE_FILE is unlinked.

Pass criteria:
  - Teardown directive emitted as expected
    ("Last active teammate task completed. Invoke Skill(\"PACT:unwatch-inbox\")...").
  - STATE_FILE is removed after Teardown skill invocation.

Failure mode: defer-Teardown over-fires (suppresses legitimate Teardown on a
standalone task). Indicates the predicate's empty-blocks check is broken.
```

### D4 — Bug A doesn't apply to different-teammate continuations

```
Setup: Dispatch Task A (backend-coder) with addBlocks=[Task B owned by
       test-engineer]. Two different teammates.

Sequence:
  1. Lead completes Task A.
  2. Observe: Teardown directive emits if count_active_tasks==0 (Task B is
     pending and non-exempt → count==1 → no Teardown). Then teammate-test
     claims Task B → re-Arm fires only if STATE_FILE absent.

Pass criteria:
  - The defer-Teardown predicate returns False (different owner) so
    cross-teammate handoffs do not get the same defer treatment.
  - Lifecycle proceeds normally per the existing Teardown count-gate.
```

### D5 — Doc-fix sanity (scripted grep PASS/FAIL)

```sh
# Run from repo root.
MARKER="wait for teammate's wake-signal SendMessage"
SURFACES=(
    "pact-plugin/protocols/pact-completion-authority.md"
    "pact-plugin/protocols/pact-protocols.md"
    "pact-plugin/agents/pact-orchestrator.md"
    "pact-plugin/skills/pact-teachback/SKILL.md"
    "pact-plugin/skills/pact-agent-teams/SKILL.md"
)

failed=0
for f in "${SURFACES[@]}"; do
    # Use grep -F (fixed-string match) to avoid the apostrophe-in-pattern
    # shell-escape pitfall — `teammate's` with a single-quoted -E pattern
    # would terminate the quoted string mid-pattern. -F treats the marker
    # as a literal byte sequence.
    if grep -Fq "$MARKER" "$f"; then
        echo "PASS: $f contains marker"
    else
        echo "FAIL: $f MISSING marker"
        failed=1
    fi
done

if [ $failed -eq 0 ]; then
    echo ""
    echo "D5 PASS: marker phrase present at all 5 surfaces"
else
    echo ""
    echo "D5 FAIL: marker phrase missing from $failed surface(s) — Bug C doc-fix incomplete"
    exit 1
fi
```

### D6 — Cross-surface anchor integrity

```sh
# The persona's cross-ref must point at the SSOT anchor slug.
grep -q '#read-trigger-precondition' pact-plugin/agents/pact-orchestrator.md \
    && echo "D6 PASS: persona cross-refs SSOT anchor" \
    || echo "D6 FAIL: persona missing cross-ref to #read-trigger-precondition"

# The SSOT must define the H3 heading that the cross-ref targets.
grep -q '^### Read-Trigger Precondition$' \
    pact-plugin/protocols/pact-completion-authority.md \
    && echo "D6 PASS: SSOT defines '### Read-Trigger Precondition' heading" \
    || echo "D6 FAIL: SSOT missing H3 heading"
```

---

## Verification matrix

Cardinality predictions are empirical, measured against the
00184b8b^ source via SOURCE-ONLY revert (the bundled-commit recipe at
the top of this runbook). Re-measure after any future hook source
change before trusting the prediction.

| Test scope | Pass criteria | Counter-test cardinality on revert |
|-----------|----------------|-------------------------------------|
| `test_has_same_teammate_continuation.py` | All pass on fix | ~34 fail, 0 collection errors (3 production-shape regression tests pass on revert; predicate-symbol AttributeError raises per-test, not at collection) |
| `test_wake_lifecycle_bug_a_defer_teardown.py` (7 tests) | All pass on fix | 2 fail, 5 pass on revert (test_secretary_bug_a_fixed_via_count_gate_post_empty_carve_out + test_defer_teardown_branch_isolated_at_post_zero; the other 5 are dual-coverage or negative pairs) |
| `test_wake_lifecycle_bug_b_rearm.py` (10 tests) | All pass on fix | 4 fail, 6 pass on revert (4 re-Arm cells; 3 audit-anchor + 3 negative tests survive) |
| `test_is_pending_to_in_progress_transition.py` (8 tests) | All pass on fix | 8 fail (predicate symbol gone; AttributeError raises per-test) |
| `test_read_trigger_precondition_pinned.py` (13 tests) | All pass on fix | up to ~13 fail per full Bug C surface revert (presence + count + drift + anchor + cross-ref pins) |
| Existing `test_inbox_wake_lifecycle_emitter.py` (full file) | All pass on fix; no regressions | 1 fail on revert (test_arm_on_create_owned_by_secretary_post_empty_carve_out — the post-empty inverted carve-out test) |
| Existing `test_inbox_wake_lifecycle_helper.py` (full file) | All pass on fix; no regressions | 5 fail on revert (post-empty inverted carve-out tests for _lifecycle_relevant + count_active_tasks) |

---

## Known limitations

1. **Secretary-on-secretary Bug A scenario is FIXED via the count gate as
   of cycle 5 (commit 8e7a073d).** The original PR landed with
   WAKE_EXCLUDED_AGENT_TYPES containing pact-secretary, which excluded
   secretary-owned tasks from `count_active_tasks` and left the eager-
   Teardown bug on secretary teachback chains; the documentation-in-code
   test was named accordingly. Cycle 5 emptied WAKE_EXCLUDED_AGENT_TYPES
   so secretary tasks now contribute to the wake tally; the count gate
   (`count_active_tasks(team) != 0`) suppresses Teardown before the defer-
   Teardown predicate is even consulted. The defer-Teardown predicate
   ALSO returns True for secretary→secretary continuations now (cell-6
   inverted in `test_has_same_teammate_continuation.py`) so defense-in-
   depth is preserved. See `test_secretary_bug_a_fixed_via_count_gate_post_empty_carve_out`
   in `test_wake_lifecycle_bug_a_defer_teardown.py` for the canonical pin.
   SELF_COMPLETE_EXEMPT_AGENT_TYPES on the self-completion side still
   contains pact-secretary (self-completion authority preserved); only
   the wake-side carve-out is empty (decoupled by design).

2. **In-session validation is impossible.** Per CLAUDE.md "Hooks cannot be
   smoke-tested against the running plugin in-session." The fix's correctness
   is verified by CI + counter-test-by-revert + this post-merge runbook only.
   Do NOT attempt to validate the running session's hook behavior — it uses
   the OLD code.

3. **Production-shape predicate uses `blocks` field, not `addBlocks`.**
   Empirical from session pact-8159e827: 24/24 task files had
   `addBlocks: null` and `blocks: [...]` populated. The predicate reads
   `blocks` first with `addBlocks` as forward-compat fallback. A future
   platform change that surfaces `addBlocks` as authoritative would require
   re-evaluating the precedence.
