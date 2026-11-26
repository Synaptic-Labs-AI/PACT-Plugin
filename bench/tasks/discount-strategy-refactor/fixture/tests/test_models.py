"""Seed tests for the cart and order value objects."""

import pytest

from pricing.models import CartItem, Order


def test_item_total_cents():
    item = CartItem("sku-1", unit_price_cents=1250, quantity=3)
    assert item.total_cents == 3750


def test_item_validation():
    with pytest.raises(ValueError):
        CartItem("", unit_price_cents=100)
    with pytest.raises(ValueError):
        CartItem("sku-1", unit_price_cents=-1)
    with pytest.raises(ValueError):
        CartItem("sku-1", unit_price_cents=100, quantity=0)
    with pytest.raises(ValueError):
        CartItem("sku-1", unit_price_cents="100")


def test_order_subtotal_and_units():
    order = Order(
        items=(
            CartItem("sku-1", unit_price_cents=1000, quantity=2),
            CartItem("sku-2", unit_price_cents=250, quantity=4),
        ),
        customer_tier="silver",
        coupon="SAVE10",
    )
    assert order.subtotal_cents == 3000
    assert order.total_units == 6
    assert order.customer_tier == "silver"
    assert order.coupon == "SAVE10"


def test_order_accepts_a_list_of_items():
    order = Order(items=[CartItem("sku-1", 500, 2)])
    assert order.items == (CartItem("sku-1", 500, 2),)
    assert order.subtotal_cents == 1000


def test_order_validation():
    with pytest.raises(ValueError):
        Order(items=(CartItem("sku-1", 100),), customer_tier="")
    with pytest.raises(ValueError):
        Order(items=(CartItem("sku-1", 100),), coupon=123)
    with pytest.raises(ValueError):
        Order(items=(CartItem("sku-1", 100), "not-an-item"))
