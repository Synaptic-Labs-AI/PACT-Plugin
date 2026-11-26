"""Behavioral tests for the discount policy and checkout integration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _add_workspace_to_path() -> None:
    here = Path(__file__).resolve()
    for candidate in here.parents[:4]:
        if (candidate / "pricing").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return


_add_workspace_to_path()


def make_order(*, tier="standard", coupon=None, items=((2000, 2),)):
    from pricing.models import CartItem, Order

    return Order(
        items=tuple(
            CartItem(f"sku-{index}", unit_price_cents=price, quantity=quantity)
            for index, (price, quantity) in enumerate(items)
        ),
        customer_tier=tier,
        coupon=coupon,
    )


def test_default_policy_order_and_names():
    from pricing.policy import DiscountPolicy, default_policy

    expected = ["tier", "coupon", "bulk", "none"]
    assert [strategy.name for strategy in default_policy().strategies] == expected
    assert [strategy.name for strategy in DiscountPolicy().strategies] == expected
    assert default_policy() is not default_policy()


def test_discount_details_agrees_with_discount_cents():
    from pricing.discounts import discount_cents, discount_details

    orders = [
        make_order(),
        make_order(tier="silver"),
        make_order(tier="gold", coupon="SAVE10"),
        make_order(tier="gold", items=((1000, 12),)),
        make_order(tier="standard", items=()),
    ]
    for order in orders:
        details = discount_details(order)
        assert details.amount_cents == discount_cents(order)
        assert 0 <= details.amount_cents <= order.subtotal_cents


def test_package_exports_public_interface():
    import pricing
    from pricing import (
        BulkOrder,
        CouponDiscount,
        DiscountDetails,
        DiscountPolicy,
        DiscountStrategy,
        NoDiscount,
        PercentDiscount,
        TierDiscount,
        default_policy,
        discount_cents,
        discount_details,
    )
    from pricing.discounts import COUPON_PERCENT, TIER_PERCENT

    order = make_order(tier="silver")
    assert discount_details(order).amount_cents == discount_cents(order) == 200
    assert isinstance(discount_details(order), DiscountDetails)
    assert isinstance(default_policy(), DiscountPolicy)
    assert TIER_PERCENT == {"standard": 0, "silver": 5, "gold": 10}
    assert COUPON_PERCENT == {"SAVE10": 10}
    for exported in (
        BulkOrder,
        CouponDiscount,
        DiscountPolicy,
        DiscountStrategy,
        NoDiscount,
        PercentDiscount,
        TierDiscount,
    ):
        assert exported is not None
    assert pricing.discount_details(order).amount_cents == 200


def test_checkout_summary_uses_winning_discount():
    from pricing.checkout import CheckoutSummary, checkout_summary

    bulk_order = make_order(tier="gold", coupon="SAVE10", items=((1000, 10),))
    assert bulk_order.subtotal_cents == 10000
    summary = checkout_summary(bulk_order)
    assert isinstance(summary, CheckoutSummary)
    assert summary.subtotal_cents == 10000
    assert summary.discount_cents == 1500
    assert summary.total_cents == 8500

    exact = make_order(tier="gold", items=((1000, 10),))
    assert checkout_summary(exact).discount_cents == 1500

    rounding = checkout_summary(make_order(tier="silver", items=((333, 3),)))
    assert rounding == CheckoutSummary(
        subtotal_cents=999, discount_cents=49, total_cents=950
    )

    empty = checkout_summary(make_order(items=()))
    assert (empty.subtotal_cents, empty.discount_cents, empty.total_cents) == (0, 0, 0)


def test_bulk_boundary_in_default_policy():
    from pricing.discounts import discount_details

    nine = make_order(items=((1000, 9),))
    assert discount_details(nine).amount_cents == 0
    ten = make_order(items=((1000, 10),))
    details = discount_details(ten)
    assert details.amount_cents == 1500
    assert details.strategy_name == "bulk"
