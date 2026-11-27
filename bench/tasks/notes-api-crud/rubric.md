# Rubric - notes-api-crud

Blind quality rubric for reviewers of the agent-under-test patch. Keep the
verifier outcome (hidden tests) as the primary signal; use this rubric to
describe quality where the verifier passes.

## Gate

- PASS only if all hidden tests pass and the untouched seed tests still pass.
- FAIL if existing public functions changed behavior, if new third-party
  dependencies were added, or if `notes_api.update_note` /
  `notes_api.delete_note` are not importable from the package root.

## Dimensions (score 1-5 each)

### 1. Functional contract (weight 40%)

5 - Update and delete behave exactly as specified, including keyword-only
    optional fields, copy-on-return, and refreshed `updated_at`.
4 - Contract met; a minor unspecified detail differs (e.g. extra keys or a
    different but descriptive error message).
3 - Main paths work; one edge case is missing or wrong.
2 - Operations exist but a central behavior is wrong (wrong return shape,
    missing id raises the wrong error, delete does not remove from listing).
1 - Not implemented or does not run.

### 2. Validation and error semantics (weight 25%)

5 - All invalid inputs raise the documented error classes, state is unchanged
    after each error, and conflict-vs-validation-vs-not-found cases are
    distinguished correctly.
4 - One minor validation gap.
3 - Validation mostly present but not-found and validation errors are
    conflated somewhere.
2 - Most invalid inputs are accepted or raise the wrong class.
1 - No validation.

### 3. Preserved behavior and API stability (weight 15%)

5 - Existing signatures, return shapes, title uniqueness, and error hierarchy
    untouched; seed tests pass.
4 - Seed tests pass with a cosmetic change a reviewer would accept.
3 - Seed tests pass only after tweaking them.
2 - Seed tests were weakened or skipped.
1 - Existing behavior broken.

### 4. Tests added (weight 10%)

5 - New tests cover update/delete happy paths, validation, conflicts, and
    not-found cases, deterministically and without relying on wall-clock
    timing.
4 - Good coverage with one missing scenario.
3 - Only happy-path tests.
2 - Tests are trivial or assertion-free.
1 - No tests added.

### 5 - Code quality and clarity (weight 10%)

5 - Small, readable, stdlib-only change that follows the existing module
    style; no duplicated validation logic; copies are returned consistently.
4 - Clear change with minor style drift.
3 - Works but repeats logic or is hard to follow.
2 - Confusing or over-engineered for the task.
1 - Unreadable or broad unnecessary refactor.

## Verdict format

`score = sum(weight * dimension_score)` (range 1.0-5.0). Report
`PASS`/`FAIL` from the gate first, then dimension scores, then at most three
concrete evidence lines (file/behavior).
