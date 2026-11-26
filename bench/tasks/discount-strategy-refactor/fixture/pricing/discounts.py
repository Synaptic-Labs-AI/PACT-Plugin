"""Discount calculation for the pricing service.

The current rules are simple: a percentage off for the customer's loyalty tier
and a percentage off for a coupon code. The customer receives whichever single
discount is larger; discounts never stack.
"""

from __future__ import annotations

from .models import Order

TIER_PERCENT: dict[str, int] = {"standard": 0, "silver": 5, "gold": 10}
COUPON_PERCENT: dict[str, int] = {"SAVE10": 10}


def percent_off_cents(subtotal_cents: int, percent: int) -> int:
    """Return 'percent' of 'subtotal_cents', rounded down to whole cents."""
    return subtotal_cents * percent // 100


def discount_cents(order: Order) -> int:
    """Return the absolute discount in whole cents for the order."""
    subtotal = order.subtotal_cents
    best = 0
    tier_percent = TIER_PERCENT.get(order.customer_tier, 0)
    if tier_percent > 0:
        best = percent_off_cents(subtotal, tier_percent)
    coupon_percent = COUPON_PERCENT.get(order.coupon or "", 0)
    if coupon_percent > 0:
        coupon_discount = percent_off_cents(subtotal, coupon_percent)
        if coupon_discount > best:
            best = coupon_discount
    return best
