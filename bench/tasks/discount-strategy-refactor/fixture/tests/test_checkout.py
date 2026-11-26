"""Seed tests for checkout totals."""

from pricing.checkout import CheckoutSummary, checkout_summary
from pricing.models import CartItem, Order


def test_single_item_summary():
    order = Order(items=(CartItem("sku-1", unit_price_cents=1500, quantity=2),))
    assert checkout_summary(order) == CheckoutSummary(
        subtotal_cents=3000, discount_cents=0, total_cents=3000
    )


def test_summary_applies_the_best_discount():
    order = Order(
        items=(CartItem("sku-1", unit_price_cents=2000, quantity=2),),
        customer_tier="gold",
    )
    assert checkout_summary(order) == CheckoutSummary(
        subtotal_cents=4000, discount_cents=400, total_cents=3600
    )


def test_multi_item_summary():
    order = Order(
        items=(
            CartItem("sku-1", unit_price_cents=1000, quantity=2),
            CartItem("sku-2", unit_price_cents=250, quantity=4),
        ),
        customer_tier="silver",
    )
    assert checkout_summary(order) == CheckoutSummary(
        subtotal_cents=3000, discount_cents=150, total_cents=2850
    )


def test_empty_order_summary():
    order = Order(items=())
    assert checkout_summary(order) == CheckoutSummary(
        subtotal_cents=0, discount_cents=0, total_cents=0
    )
