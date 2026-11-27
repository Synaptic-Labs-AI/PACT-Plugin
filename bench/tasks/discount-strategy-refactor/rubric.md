# Blind quality rubric: discount-strategy-refactor

Score the agent's work without looking at the hidden tests. Use the patch, the
repository state, and the agent's own test run as evidence. Each criterion is
scored 0-4; the weighted total is the score.

## Criteria and weights

### 1. Strategy interface (30%)

- 4: a clean `DiscountStrategy` base class with `name` and `amount_cents`;
  subclasses cover tier, coupon, percent, no-discount, and a configurable bulk
  rule; a brand-new strategy class can be plugged in without touching policy or
  checkout code.
- 3: interface exists and works, but one rule is implemented awkwardly or the
  extension point needs edits elsewhere.
- 2: strategies exist but the policy still hard-codes rule selection.
- 1: partial/awkward attempt.
- 0: no strategy abstraction.

### 2. Policy selection semantics (25%)

- 4: best single discount, ties resolved by strategy order, amounts clamped to
  `[0, subtotal]`, no-discount maps to `("none", 0)`, default order is
  tier/coupon/bulk/none, and custom strategy lists are honored.
- 3: works with one minor semantic gap.
- 2: selection works but clamping, ties, or default order are wrong.
- 1: selection incorrect or stacking occurs.
- 0: no policy object.

### 3. Behavior preservation and bulk rule (20%)

- 4: every old price is unchanged (tiers, coupon, floor rounding, no stacking)
  and the bulk rule triggers exactly at the threshold with the configured
  percentage; checkout totals use the winning discount; seed tests untouched
  and green.
- 3: old behavior preserved, bulk rule has a minor edge-case bug.
- 2: bulk rule works but some old behavior changed, or seed tests were edited.
- 1: multiple behavior regressions.
- 0: old behavior broken.

### 4. Public API and module layout (15%)

- 4: new modules and classes named as requested; `discount_details` and
  `discount_cents` on `pricing.discounts`; `TIER_PERCENT`/`COUPON_PERCENT`
  still importable; package-level re-exports work; no circular imports.
- 3: minor naming/layout deviation.
- 2: interface usable but several names or exports missing.
- 1: most requested names missing.
- 0: public API broken.

### 5. Tests, quality, and process (10%)

- 4: stdlib only, readable typed code, new focused tests for the bulk rule and
  custom strategies, work committed on a feature branch with no push/PR/merge.
- 3: solid, minor style or test gaps.
- 2: works but no meaningful new tests or noticeably messy.
- 1: poor quality or seed tests weakened.
- 0: unreadable or violates constraints.

## Gates and labels

- All hidden tests must pass before the score can exceed 2.
- Discount stacking, a broken seed test, or an edited seed test caps the score
  at 2.
- A new runtime dependency caps the score at 1.
- 3.5-4: excellent, ready to merge. 2.5-3.4: solid, minor gaps. 1.5-2.4:
  acceptable internal only. 0.5-1.4: weak. 0-0.4: broken.
