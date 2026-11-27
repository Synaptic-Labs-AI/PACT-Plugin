"""Invoice and tax calculations.

Every monetary value is an integer number of cents. Percentage rates are
normalised to :class:`~decimal.Decimal` before any arithmetic so callers can
pass `8.25`, `"8.25"`, or `Decimal("8.25")` interchangeably.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

RateInput = Decimal | int | float | str


def parse_rate_percent(rate: Any) -> Decimal:
    """Return *rate* as a non-negative Decimal percentage."""
    if isinstance(rate, bool):
        raise ValueError("rate must be a number, not a bool")
    try:
        value = Decimal(str(rate))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid rate: {rate!r}") from None
    if not value.is_finite():
        raise ValueError(f"invalid rate: {rate!r}")
    if value < 0:
        raise ValueError(f"rate must be non-negative: {rate!r}")
    return value


def round_cents(amount: Any) -> int:
    """Round a cents amount to a whole cent."""
    if isinstance(amount, Decimal):
        value = amount
    else:
        value = Decimal(str(amount))
    return int(round(value))


def tax_amount(subtotal_cents: int, rate_percent: RateInput) -> int:
    """Return the tax in cents owed on *subtotal_cents*."""
    rate = parse_rate_percent(rate_percent)
    return round_cents(Decimal(subtotal_cents) * rate / Decimal(100))


def apply_discount(amount_cents: int, percent: RateInput) -> int:
    """Return the discount in cents for *amount_cents* at *percent*."""
    rate = parse_rate_percent(percent)
    exact = Decimal(amount_cents) * rate / Decimal(100)
    return int(exact)


def invoice_totals(
    subtotal_cents: int,
    tax_rate_percent: RateInput,
    discount_percent: RateInput = 0,
) -> dict[str, int]:
    """Return the cents breakdown for a single invoice."""
    subtotal = int(subtotal_cents)
    discount = apply_discount(subtotal, discount_percent)
    taxable = subtotal - discount
    tax = tax_amount(taxable, tax_rate_percent)
    total = taxable + tax
    return {
        "subtotal": subtotal,
        "discount": discount,
        "taxable": taxable,
        "tax": tax,
        "total": total,
    }
