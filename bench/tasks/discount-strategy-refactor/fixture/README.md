# pricing

Small checkout-pricing library used by the storefront team. It is pure Python
with no network or database access: callers build an `Order`, then read the
discount and checkout totals.

## Layout

- `pricing/models.py` - `CartItem` and `Order` value objects (money is integer
  cents).
- `pricing/discounts.py` - discount calculation (loyalty tier and coupon code).
- `pricing/checkout.py` - `CheckoutSummary` and `checkout_summary()`.
- `tests/` - pytest suite for the current behavior.

## Running the tests

    python -m pytest -q

Python 3.12, standard library only. `pytest` is available in the project
environment.
