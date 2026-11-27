"""Tie rounding for the public round_cents helper."""

from decimal import Decimal

import pytest

from invoice import round_cents


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.5", 1),
        ("1.5", 2),
        ("2.5", 3),
        ("10.5", 11),
        ("50.5", 51),
        ("52.5", 53),
        ("100.5", 101),
    ],
)
def test_exact_half_cents_round_up(raw, expected):
    assert round_cents(Decimal(raw)) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("2.49"), 2),
        (Decimal("2.51"), 3),
        ("7.49", 7),
        (7.51, 8),
        (0.4, 0),
        (0.6, 1),
    ],
)
def test_values_off_the_half_cent_still_round_to_nearest(raw, expected):
    assert round_cents(raw) == expected


def test_whole_cent_amounts_are_not_changed_by_rounding():
    for amount in (0, 1, 3, 500, 12345):
        assert round_cents(amount) == amount
