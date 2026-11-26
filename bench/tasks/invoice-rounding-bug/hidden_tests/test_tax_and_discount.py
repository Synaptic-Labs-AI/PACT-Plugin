"""Tax and discount amounts with fractional cents."""

import pytest

from invoice import apply_discount, tax_amount


@pytest.mark.parametrize(
    ("subtotal_cents", "rate_percent", "expected"),
    [
        (1010, 5, 51),          # 50.5 cents
        (1050, 5, 53),          # 52.5 cents
        (10, 5, 1),             # 0.5 cents
        (50, 5, 3),             # 2.5 cents
        (1005, 10, 101),        # 100.5 cents
        (1030, 5, 52),          # 51.5 cents (already even)
        (200, "8.25", 17),      # 16.5 cents at a decimal rate
        (2000, 10, 200),        # exact whole cents
    ],
)
def test_tax_amount_rounds_half_cents_up(subtotal_cents, rate_percent, expected):
    assert tax_amount(subtotal_cents, rate_percent) == expected


@pytest.mark.parametrize(
    ("amount_cents", "percent", "expected"),
    [
        (1005, 10, 101),        # 100.5 cents
        (1030, 5, 52),          # 51.5 cents
        (50, 5, 3),             # 2.5 cents
        (1010, 5, 51),          # 50.5 cents
        (10, 5, 1),             # 0.5 cents
        (200, "8.25", 17),      # 16.5 cents at a decimal rate
        (10000, 8, 800),        # exact whole cents
        (0, 10, 0),             # zero amount
    ],
)
def test_apply_discount_rounds_half_cents_up(amount_cents, percent, expected):
    assert apply_discount(amount_cents, percent) == expected


def test_tax_amount_never_truncates_a_fractional_cent_downwards():
    # 51.5 cents must become 52, while 40.4 rounds to 40.
    assert tax_amount(1030, 5) == 52
    assert tax_amount(808, 5) == 40
