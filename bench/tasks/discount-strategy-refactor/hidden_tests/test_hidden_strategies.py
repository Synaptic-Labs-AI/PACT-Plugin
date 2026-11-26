"""Behavioral tests for the discount strategy interface and rules."""

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


def details_for(order, policy=None):
    from pricing.discounts import discount_details

    if policy is None:
        return discount_details(order)
    return policy.discount_details(order)


@pytest.mark.parametrize(
    ("tier", "expected_amount", "expected_name"),
    [("standard", 0, "none"), ("silver", 200, "tier"), ("gold", 400, "tier"), ("bronze", 0, "none")],
)
def test_existing_tier_rules_preserved(tier, expected_amount, expected_name):
    order = make_order(tier=tier)
    assert order.subtotal_cents == 4000
    details = details_for(order)
    assert details.amount_cents == expected_amount
    assert details.strategy_name == expected_name
    from pricing.discounts import discount_cents

    assert discount_cents(order) == expected_amount


def test_existing_coupon_rules_preserved():
    save10 = details_for(make_order(coupon="SAVE10"))
    assert save10.amount_cents == 400
    assert save10.strategy_name == "coupon"
    unknown = details_for(make_order(coupon="SAVE99"))
    assert unknown.amount_cents == 0
    assert unknown.strategy_name == "none"
    absent = details_for(make_order(coupon=None))
    assert absent.amount_cents == 0


def test_floor_rounding_preserved():
    from pricing.discounts import discount_cents

    silver = make_order(tier="silver", items=((333, 3),))
    assert silver.subtotal_cents == 999
    assert details_for(silver).amount_cents == 49
    assert discount_cents(silver) == 49
    coupon = make_order(coupon="SAVE10", items=((333, 3),))
    assert details_for(coupon).amount_cents == 99


def test_best_single_discount_never_stacks():
    tie = details_for(make_order(tier="gold", coupon="SAVE10"))
    assert tie.amount_cents == 400
    assert tie.strategy_name == "tier"
    coupon_wins = details_for(make_order(tier="silver", coupon="SAVE10"))
    assert coupon_wins.amount_cents == 400
    assert coupon_wins.strategy_name == "coupon"


def test_bulk_order_strategy_default_threshold():
    from pricing.strategies import BulkOrder

    strategy = BulkOrder()
    ten_units = make_order(items=((1000, 10),))
    assert strategy.amount_cents(ten_units) == 1500
    nine_units = make_order(items=((1000, 9),))
    assert strategy.amount_cents(nine_units) == 0
    mixed = make_order(items=((600, 3), (700, 3), (500, 4)))
    assert mixed.total_units == 10
    assert mixed.subtotal_cents == 5900
    assert strategy.amount_cents(mixed) == 885
    bulk_wins = details_for(make_order(tier="gold", items=((1000, 10),)))
    assert bulk_wins.amount_cents == 1500
    assert bulk_wins.strategy_name == "bulk"
    twelve = details_for(make_order(tier="gold", items=((1000, 12),)))
    assert twelve.amount_cents == 1800
    assert twelve.strategy_name == "bulk"


def test_bulk_order_strategy_custom_parameters():
    from pricing.policy import DiscountPolicy
    from pricing.strategies import BulkOrder, NoDiscount

    policy = DiscountPolicy([BulkOrder(min_units=5, percent=20), NoDiscount()])
    five_units = make_order(items=((500, 5),))
    details = policy.discount_details(five_units)
    assert details.amount_cents == 500
    assert details.strategy_name == "bulk"
    four_units = make_order(items=((500, 4),))
    assert policy.discount_details(four_units).amount_cents == 0


def test_percent_discount_strategy():
    from pricing.strategies import PercentDiscount

    strategy = PercentDiscount("flash", 25)
    assert strategy.name == "flash"
    assert strategy.amount_cents(make_order(items=((2000, 1),))) == 500
    with pytest.raises(ValueError):
        PercentDiscount("bad", -1)
    with pytest.raises(ValueError):
        PercentDiscount("bad", 101)
    with pytest.raises(ValueError):
        PercentDiscount("bad", 50.5)
    with pytest.raises(ValueError):
        PercentDiscount("", 10)


def test_no_discount_strategy():
    from pricing.strategies import NoDiscount

    strategy = NoDiscount()
    assert strategy.name == "none"
    assert strategy.amount_cents(make_order(tier="gold", items=((5000, 7),))) == 0


def test_strategy_validation():
    from pricing.strategies import BulkOrder

    with pytest.raises(ValueError):
        BulkOrder(min_units=0)
    with pytest.raises(ValueError):
        BulkOrder(min_units=-2)
    with pytest.raises(ValueError):
        BulkOrder(percent=101)
    with pytest.raises(ValueError):
        BulkOrder(percent=-5)


def test_custom_strategy_extension_point():
    from pricing.policy import DiscountPolicy
    from pricing.strategies import DiscountStrategy, TierDiscount

    class FlatFee(DiscountStrategy):
        name = "flat-fee"

        def amount_cents(self, order):
            return 500

    policy = DiscountPolicy([FlatFee(), TierDiscount()])
    standard = make_order()
    assert policy.discount_details(standard).amount_cents == 500
    assert policy.discount_details(standard).strategy_name == "flat-fee"
    expensive_gold = make_order(tier="gold", items=((4000, 2),))
    assert expensive_gold.subtotal_cents == 8000
    tier_wins = policy.discount_details(expensive_gold)
    assert tier_wins.amount_cents == 800
    assert tier_wins.strategy_name == "tier"
    with pytest.raises(TypeError):
        DiscountPolicy([object()])


def test_policy_clamps_strategy_amounts():
    from pricing.policy import DiscountPolicy
    from pricing.strategies import DiscountStrategy, NoDiscount

    class Huge(DiscountStrategy):
        name = "huge"

        def amount_cents(self, order):
            return order.subtotal_cents + 5000

    class Negative(DiscountStrategy):
        name = "negative"

        def amount_cents(self, order):
            return -100

    order = make_order()
    assert order.subtotal_cents == 4000
    huge = DiscountPolicy([Huge()]).discount_details(order)
    assert huge.amount_cents == 4000
    assert huge.strategy_name == "huge"
    negative = DiscountPolicy([Negative(), NoDiscount()]).discount_details(order)
    assert negative.amount_cents == 0
    assert negative.strategy_name == "none"
