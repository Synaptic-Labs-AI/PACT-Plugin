# Rubric - invoice-rounding-bug

Blind quality rubric for reviewers of the agent-under-test patch. The verifier
outcome (hidden tests) is the primary signal; use this rubric to describe
quality where the verifier passes.

## Gate

- PASS only if all hidden tests pass and the untouched seed tests still pass.
- FAIL if public function names/signatures/return shapes changed, if
  third-party dependencies were added, if integer-cent results regressed, or
  if rounding is still banker's/truncating.

## Dimensions (score 1-5 each)

### 1. Rounding correctness (weight 45%)

5 - All half-cent ties round up in `round_cents`, `tax_amount`,
    `apply_discount`, and `invoice_totals`; non-tie values still round to
    nearest; exact whole cents are unchanged.
4 - Main bug fixed everywhere; one helper or one non-tie path still uses the
    old behaviour.
3 - Tax fixed but discount truncation remains (or vice versa).
2 - Only some of the reported examples produce the expected output.
1 - Bug not fixed or new rounding errors introduced.

### 2. No double rounding / correct composition (weight 20%)

5 - Discount is rounded once into whole cents, taxable is the rounded
    remainder, tax is rounded once from the taxable amount, and total is the
    sum of the published whole-cent values, matching the documented example.
4 - Composition correct; a minor unrounded intermediate survives without
    changing visible output.
3 - Correct final totals by luck in the tested cases, but intermediate
    rounding order is muddled.
2 - Re-rounds already-rounded values or computes tax before the discount.
1 - Inconsistent totals (breakdown does not add up).

### 3. Public API and compatibility (weight 20%)

5 - All public signatures, return shapes, validation, and error types
    unchanged; seed tests pass untouched.
4 - Seed tests pass; cosmetic change a reviewer would accept.
3 - Seed tests pass only after editing them.
2 - Seed tests weakened or skipped.
1 - Public API broken.

### 4. Tests added (weight 10%)

5 - New tests pin every reported example, both tie directions, a decimal
    rate, and exact-cent regression cases; deterministic.
4 - Good coverage with one reported case missing.
3 - Only the two headline examples are tested.
2 - Trivial or assertion-free tests.
1 - No tests added.

### 5. Code quality and clarity (weight 5%)

5 - Small, readable, stdlib-only change; a single shared half-up rounding
    helper is used consistently.
4 - Clear change with minor duplication.
3 - Works but duplicates rounding logic in several places.
2 - Hard to follow or over-engineered.
1 - Unreadable or unrelated refactor.

## Verdict format

`score = sum(weight * dimension_score)` (range 1.0-5.0). Report
`PASS`/`FAIL` from the gate first, then dimension scores, then at most three
concrete evidence lines (file/behavior).
