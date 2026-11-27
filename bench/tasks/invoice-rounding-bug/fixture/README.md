# invoice

A tiny invoice and tax calculator. Python 3.12, standard library only.

All monetary amounts are integer cents. Rates are percentages, so `8.25`
means 8.25 percent.

## Layout

- `invoice/` - calculator package.
- `tests/` - pytest suite for the current public API.

## Run the tests

    python -m pytest -q

## Public API

- `parse_rate_percent(rate) -> Decimal` - normalise `int`, `float`, `str`, or
  `Decimal` percentages to `Decimal`; negative or invalid rates raise
  `ValueError`.
- `round_cents(amount) -> int` - round a cents amount to a whole cent.
- `tax_amount(subtotal_cents, rate_percent) -> int` - tax in cents.
- `apply_discount(amount_cents, percent) -> int` - discount in cents.
- `invoice_totals(subtotal_cents, tax_rate_percent, discount_percent=0) -> dict`
  - returns integer cents for `subtotal`, `discount`, `taxable`, `tax`,
  `total`. Tax is charged on the taxable amount (subtotal minus discount).
