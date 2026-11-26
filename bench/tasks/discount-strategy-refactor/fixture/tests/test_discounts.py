"""Seed tests for the current discount rules."""

import pytest

from pricing.discounts import discount_cents
from pricing.models import CartItem, Order


def make_order(*, tier="standard", coupon=None, price=2000, quantity=2) -> Order:
    return Order(
        items=(CartItem("sku-1", unit_price_cents=price, quantity=quantity),),
        customer_tier=tier,
        coupon=coupon,
    )


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("standard", 0), ("silver", 200), ("gold", 400)],
)
def test_tier_discounts(tier, expected):
    order = make_order(tier=tier)
    assert order.subtotal_cents == 4000
    assert discount_cents(order) == expected


def test_unknown_tier_has_no_discount():
    assert discount_cents(make_order(tier="bronze")) == 0


def test_save10_coupon():
    assert discount_cents(make_order(coupon="SAVE10")) == 400


def test_unknown_coupon_has_no_discount():
    assert discount_cents(make_order(coupon="SAVE99")) == 0


def test_best_single_discount_never_stacks():
    order = make_order(tier="gold", coupon="SAVE10")
    assert discount_cents(order) == 400


def test_discount_rounds_down_to_whole_cents():
    items = (CartItem("sku-1", unit_price_cents=333, quantity=3),)
    order = Order(items=items, customer_tier="silver")
    assert order.subtotal_cents == 999
    assert discount_cents(order) == 49
