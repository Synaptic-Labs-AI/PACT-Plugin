"""Checkout totals for the pricing service."""

from __future__ import annotations

from dataclasses import dataclass

from .discounts import discount_cents
from .models import Order


@dataclass(frozen=True)
class CheckoutSummary:
    subtotal_cents: int
    discount_cents: int
    total_cents: int


def checkout_summary(order: Order) -> CheckoutSummary:
    """Return the priced summary for the order."""
    subtotal = order.subtotal_cents
    discount = min(discount_cents(order), subtotal)
    return CheckoutSummary(
        subtotal_cents=subtotal,
        discount_cents=discount,
        total_cents=subtotal - discount,
    )
