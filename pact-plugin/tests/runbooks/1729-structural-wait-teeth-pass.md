# Runbook: Structural Wait Mechanism — Teeth-Pass Enumeration Record

**Date:** 2026-11-21 (remediation cycle 1, PR #1730 review)
**Tree:** `issue-1729-structural-wait` @ `17e34d3e`; reviewer mutations ran in
detached `/tmp` worktrees, never the shared checkout, with `git checkout --` +
`git status`/diff verification after each (byte-exact restore).
**Operator:** test-reviewer (design + independent re-derivation); original
teeth pass by the TEST-phase builder (test-engineer).

**Why this file exists:** the TEST-phase HANDOFF recorded the original
teeth pass as a kill multiset (`{2,1,2,2,1,1,1,1}`) plus two named
prediction misses, but NOT the per-mutation sites — and no committed
artifact enumerated them, so the pass was not re-derivable by a reviewer
(review finding S3). This file is the committed enumeration surface for
the structural-wait teeth passes: what is recoverable of the original 8,
plus the full site/class/prediction/observed record of the reviewer's
independent 7. Convention going forward: every teeth pass appends its
mutations here (file:line at the recorded tree, mutation class,
prediction, observed kills) BEFORE staging.

---

## Original teeth pass (TEST phase, builder) — as-recorded provenance

- 8 source-only mutations, kills `{2,1,2,2,1,1,1,1}`, each predicted then
  observed, tree byte-identical after each.
- Per-mutation sites were not committed and are NOT re-derivable from any
  artifact (the gap this file closes). Recorded alongside them:
  - **M2 (prediction miss, fixed at the arm):** a registry arm drove
    `_wait_arm_advisory` directly and never exercised the shipped call
    sites — 0 kills as first drafted; rebuilt as three end-to-end
    `evaluate_lifecycle` arms (the shipped call-site literals are what a
    rename can silently orphan).
  - **M7 (prediction miss, fixed at the arm):** the corrupt-boundary heal
    was timed after poll 3 (mutation-inert — the arm stayed green for the
    wrong reason); retimed to land between polls 2 and 3.
- Seam coverage implied by the surviving arms: exit-contract constants,
  predicate string-gate, corrupt tolerance, deadline defaults, emission
  pins, carve-out classification.

## Reviewer teeth pass (2026-11-21) — full enumeration

Prediction-first: each row was predicted before running and the observed
kill set matched, except M-a where the prediction UNDER-counted (5
predicted, 6 observed — an under-prediction still demonstrates coupling;
over-predictions would not).

| # | Site (at 17e34d3e) | Mutation | Predicted | Observed kills |
|---|--------------------|----------|-----------|----------------|
| M-a | `hooks/shared/turn_end_gate.py:93` | classifier `return isinstance(...)` → `return False` (carve-out off) | 5 | 6: `test_emitted_advisory_command_satisfies_the_carveout_shape`, `test_outlet_template_substitutes_to_the_carveout_shape`, `test_watcher_does_not_block_lead_turn_end`, `test_watcher_exempt_real_job_still_blocks_on_the_real_job`, `test_duplicate_watcher_re_arm_both_exempt`, `test_is_wait_watcher_command_shape_table` |
| M-b | `hooks/shared/wait_target.py:76` | `return isinstance(produced, str) and produced.strip() != ""` → `return produced is not None` (string-gate off) | 5 | 5: `test_handoff_predicate_non_string_or_empty_produced_is_absent` params `[]` (empty string), `[   ]` (whitespace), `[produced3]` (list), `[0]`, `[False]` (the `None` param correctly stays green) |
| M-c | `hooks/pact_wait.py:54` | `MAX_CONSECUTIVE_CORRUPT_READS = 2` → `3` | 1 | 1: `test_second_consecutive_corrupt_read_is_terminal` flips to FIRED |
| M-d | `hooks/pact_wait.py:166` | else-branch `corrupt_streak = 0` → `pass` (streak never resets) | 1 | 1: `test_alternating_corrupt_reads_expire_never_turn_terminal` flips to TERMINAL |
| M-e | `hooks/pact_wait.py:48` | `POLL_INTERVAL_SECONDS = 5.0` → `500.0` | **0** | **0 — the zero-kill finding** (review S2/S3): the live cadence was unteethed; closed this cycle by `tests/test_wait_poll_teeth.py` (landing controls below) |
| M-f | `hooks/task_lifecycle_gate.py:1444` | advisory `--deadline 1800` → `900` | 2 | 2: `test_advisory_message_pinned_verbatim`, `test_deadline_defaults_pinned_on_both_surfaces` |
| M-g | `hooks/pact_wait.py:57` | `EXIT_EXPIRED = 3` → `5` | 1 | 1: `test_exit_class_numbers_are_pinned` (constant-consuming arms stay green by design — that is why the literal pin exists) |

Total observed: 16 kills across 6 armed classes, one zero-kill finding.

### Landing controls for the S3 fix (`tests/test_wait_poll_teeth.py`)

Each mutation re-applied against the NEW teeth file, scoped run, restored:

| Mutation | Predicted | Observed |
|----------|-----------|----------|
| `POLL_INTERVAL_SECONDS` 5.0 → 500.0 | 1 (magnitude arm) | 1 failed — magnitude arm |
| `POLL_INTERVAL_SECONDS` 5.0 → 0.5 | 1 (magnitude arm; the hardcoded documented value defeats self-confirmation) | 1 failed — magnitude arm |
| `time.sleep(min(poll_interval, remaining))` → `time.sleep(poll_interval)` (truncation removed) | 1 (truncation arm) | 1 failed — truncation arm |

## Teeth pass (2026-11-24) — the list-typed `produced` reversal

**Tree:** `issue-1729-structural-wait` @ `235226f8`; mutations ran in
git-archive copies of that commit, never the shared checkout. **Operator:**
test-reviewer.

**What changed since M-b:** row M-b above records a string-only `handoff`
predicate, under which a list-typed `produced` read as absent (its
`[produced3]` kill was a list). At `235226f8` the predicate wakes on a
list holding at least one non-empty string: a handoff in that shape is
still flagged by the schema advisory, but the lead is woken to read it
instead of the watcher burning its deadline to a false EXPIRED. An empty
list, or one holding only empty, whitespace-only or non-string items, still
reads as absent. Row M-b stays as the 2026-11-21 record; the two rows below
are the mutations that guard the current predicate.

Prediction-first; each observed kill set matched its prediction exactly.
Scope: `tests/test_pact_wait.py`, `tests/test_handoff_schema.py`,
`tests/test_structural_wait_matrix.py` (unmutated copy: 217 passed).

| # | Site (at 235226f8) | Mutation | Predicted | Observed kills |
|---|--------------------|----------|-----------|----------------|
| M-h | `hooks/shared/wait_target.py:129-130` | list handling removed: `items = …; return any(…)` → `return isinstance(produced, str) and produced.strip() != ""` (the 2026-11-21 string-only check) | 2 | 2: `test_handoff_predicate_string_or_non_empty_list_produced_is_present` params `[produced1]` (`["a summary"]`) and `[produced2]` (`["", "f.py"]`); the plain-string param stays green |
| M-i | `hooks/shared/wait_target.py:130` | item string-gate off: `isinstance(item, str) and item.strip() != ""` → `item is not None` | 8 | 8: `test_handoff_predicate_non_string_or_empty_produced_is_absent` params `[]` (empty string), `[   ]` (whitespace), `[produced4]` (`[""]`), `[produced5]` (`["  ", None]`), `[produced6]` (`[["nested"]]`), `[0]`, `[False]`, `[produced9]` (`{"a": "b"}`); `None` and `[produced3]` (`[]`) correctly stay green |

Total observed: 10 kills across 2 classes (list acceptance, item string
gate), no zero-kill mutation.

## Rename map for the 2026-11-21 reviewer rows (as of a40fb8b8)

Rows M-a to M-g above name the arms as they were at `17e34d3e` and stay as
recorded. Later cycles renamed or replaced some arms. This map gives the
current arm for each name those rows cite; a name not listed is unchanged.
Sites (file:line) have also moved, so read a row's site at its own tree.

| Row | Name at 17e34d3e | Current arm |
|-----|------------------|-------------|
| M-a | `test_outlet_template_substitutes_to_the_carveout_shape` | replaced by `test_every_emitted_command_satisfies_the_carveout` (with `test_every_emitted_command_with_a_composed_tail_counts` and `test_emitters_use_the_one_builder`), `tests/test_structural_wait_matrix.py` |
| M-a | `test_emitted_advisory_command_satisfies_the_carveout_shape`, `test_watcher_does_not_block_lead_turn_end`, `test_watcher_exempt_real_job_still_blocks_on_the_real_job`, `test_duplicate_watcher_re_arm_both_exempt`, `test_is_wait_watcher_command_shape_table` | unchanged |
| M-b | `test_handoff_predicate_non_string_or_empty_produced_is_absent` | same name; its parameters changed when a non-empty list began to count as present (see the 2026-11-24 entry) |
| M-c | `test_second_consecutive_corrupt_read_is_terminal` | unchanged |
| M-d | `test_alternating_corrupt_reads_expire_never_turn_terminal` | `test_alternating_corrupt_reads_never_turn_terminal`, `tests/test_pact_wait.py` (the scripted twin is `test_a_good_read_resets_the_bad_read_streak`) |
| M-e | (no arm; the zero-kill finding) | closed by `tests/test_wait_poll_teeth.py` |
| M-f | `test_advisory_message_pinned_verbatim`, `test_deadline_defaults_pinned_on_both_surfaces` | unchanged |
| M-g | `test_exit_class_numbers_are_pinned` | unchanged |
