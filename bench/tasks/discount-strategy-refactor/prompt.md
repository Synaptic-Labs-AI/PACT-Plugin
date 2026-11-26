# Refactor pricing discounts onto a strategy interface

`pricing` is a small standard-library checkout library. Discounts live in
`pricing/discounts.py` as an if/elif function: a percentage off for the
customer's loyalty tier, a percentage off for a coupon code, and the customer
gets whichever single discount is larger. Nothing stacks.

The catalogue team needs a new bulk-order discount, and product wants to add and
remove discount rules without editing the calculation function. Refactor the
discount logic onto a strategy interface, add the bulk-order rule, and keep
every existing price exactly the same.

Implement the following in the repository in your workspace.

## New module: `pricing/strategies.py`

- `DiscountStrategy`: abstract base class. Instances expose a readable
  `name` (string) and `amount_cents(order) -> int`, the absolute discount in
  whole cents. Subclassing it and implementing `amount_cents` must be enough to
  add a new rule; setting a class-level `name` must override the default.
- `NoDiscount()`: `name == "none"`, always returns 0.
- `PercentDiscount(name, percent)`: `name` is the given string;
  `amount_cents` is `order.subtotal_cents * percent // 100` (rounded down to
  whole cents). `percent` must be an integer from 0 to 100 and `name` a
  non-empty string, otherwise raise `ValueError`.
- `TierDiscount()`: `name == "tier"`. Keeps the current tier table:
  standard 0%, silver 5%, gold 10%; unknown tiers get 0%.
- `CouponDiscount()`: `name == "coupon"`. Keeps the current coupon table:
  SAVE10 10%; no coupon or an unknown coupon gives 0%.
- `BulkOrder(min_units=10, percent=15)`: `name == "bulk"`. Applies `percent` off
  the subtotal only when the order's total item quantity is at least
  `min_units`, otherwise 0. `min_units` must be a positive integer and
  `percent` an integer from 0 to 100, otherwise raise `ValueError`.
- The tier and coupon tables keep their current values, and
  `TIER_PERCENT`/`COUPON_PERCENT` must remain importable from
  `pricing.discounts`.

## New module: `pricing/policy.py`

- `DiscountDetails`: frozen dataclass with `strategy_name: str` and
  `amount_cents: int`.
- `DiscountPolicy(strategies=None)`: takes an iterable of strategies and keeps
  them, in order, as a tuple on `.strategies`. `None` builds the default rule
  set. Any non-`DiscountStrategy` in the list raises `TypeError`.
  - Clamp every strategy's amount to the range `[0, order.subtotal_cents]`.
    Largest clamped amount wins; ties go to the strategy earlier in the list.
    If nothing offers more than zero, the result is
    `DiscountDetails("none", 0)` regardless of which strategies are configured.
  - `discount_details(order) -> DiscountDetails` and
    `discount_cents(order) -> int`.
- The default rule order is: `TierDiscount`, `CouponDiscount`, `BulkOrder`,
  `NoDiscount`.
- `default_policy()` returns a new default `DiscountPolicy` each call.

## Existing modules

- `pricing/discounts.py`: keep `discount_cents(order)` working for existing
  callers, and add `discount_details(order) -> DiscountDetails` that uses the
  default policy. `TIER_PERCENT` and `COUPON_PERCENT` stay importable here.
- `pricing/checkout.py`: keep `checkout_summary(order) -> CheckoutSummary` with
  the same fields and behavior; totals must use the same winning discount.
- `pricing/__init__.py`: re-export the public names so callers can do
  `from pricing import discount_details, DiscountPolicy, DiscountDetails,
  default_policy, DiscountStrategy, TierDiscount, CouponDiscount, BulkOrder,
  PercentDiscount, NoDiscount`.

## Behavior that must not change

- standard 0%, silver 5%, gold 10%, SAVE10 10%, single best discount (no
  stacking), integer floor rounding (for example 999 cents at silver is 49
  cents of discount).
- The existing seed tests must keep passing unchanged; add tests for the new
  behavior.

## Constraints

- Python 3.12, standard library only (no third-party runtime packages).
- Work autonomously: do not ask questions. Commit your work on a feature
  branch. Do not push, open a PR, or merge.
- Run the suite with `python -m pytest -q`.
