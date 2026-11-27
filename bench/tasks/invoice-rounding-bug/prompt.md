# Fix half-cent rounding in the invoice calculator

You are working on `invoice`, a small invoice and tax calculator for Python
3.12 (standard library only). All monetary values are integer cents; rates are
percentages such as `8.25` meaning 8.25 percent.

The public API in `invoice` is:

- `parse_rate_percent(rate) -> Decimal`
- `round_cents(amount) -> int`
- `tax_amount(subtotal_cents, rate_percent) -> int`
- `apply_discount(amount_cents, percent) -> int`
- `invoice_totals(subtotal_cents, tax_rate_percent, discount_percent=0) -> dict`
  returning integer cents for `subtotal`, `discount`, `taxable`, `tax`,
  `total`

The seed pytest suite under `tests/` passes today.

Finance reports that invoices are wrong whenever an amount lands on half a
cent, and whenever a discount has a fractional cent. The agreed business rule
is:

- Every money amount in cents is rounded to the nearest whole cent, and an
  exact half cent rounds **up** (away from zero for the non-negative amounts
  this service handles).
- Round only the amounts that are finalised; do not round intermediate exact
  arithmetic and then round again.
- `tax_amount` and `invoice_totals` charge tax on the taxable amount
  (subtotal minus discount), as they do today.

## Reported examples (current output -> expected output)

- `tax_amount(1010, 5)` returns `50`; should be `51` (50.5 cents).
- `tax_amount(1050, 5)` returns `52`; should be `53` (52.5 cents).
- `tax_amount(200, "8.25")` returns `16`; should be `17` (16.5 cents).
- `apply_discount(1005, 10)` returns `100`; should be `101` (100.5 cents).
- `apply_discount(1030, 5)` returns `51`; should be `52` (51.5 cents).
- `invoice_totals(1005, 10, 10)` returns `{"subtotal": 1005, "discount":
  100, "taxable": 905, "tax": 90, "total": 995}`; it should return
  `{"subtotal": 1005, "discount": 101, "taxable": 904, "tax": 90,
  "total": 994}`.
- `round_cents` shows the same problem directly: an exact half such as
  `Decimal("50.5")` must become `51`, not `50`.

## Requirements

- Fix the rounding in all four public helpers that produce cents:
  `round_cents`, `tax_amount`, `apply_discount`, and the totals produced by
  `invoice_totals`.
- Results that are already exact whole cents must not change.
- Keep every public function name, signature, and return shape unchanged.
  Keep the existing input validation and error types (negative or invalid
  rates raise `ValueError`).
- Python 3.12 standard library only; no new dependencies.
- Add pytest tests under `tests/` covering the reported examples and half-cent
  boundaries, and run them with `python -m pytest -q`. The seed tests must keep
  passing.

## Delivery

Work autonomously; do not ask questions. Commit your work on a feature branch;
do not push, open a PR, or merge.
