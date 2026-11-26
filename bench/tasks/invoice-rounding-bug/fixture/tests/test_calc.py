from decimal import Decimal

import pytest

from invoice import (
    apply_discount,
    invoice_totals,
    parse_rate_percent,
    round_cents,
    tax_amount,
)


def test_round_cents_keeps_whole_cents():
    assert round_cents(3) == 3
    assert round_cents(500) == 500
    assert round_cents(Decimal("42")) == 42


def test_round_cents_rounds_to_the_nearest_cent():
    assert round_cents(Decimal("2.4")) == 2
    assert round_cents(Decimal("2.6")) == 3
    assert round_cents("7.49") == 7
    assert round_cents(7.51) == 8


def test_tax_amount_on_whole_cent_results():
    assert tax_amount(2000, 10) == 200
    assert tax_amount(5000, 20) == 1000
    assert tax_amount(0, 8.25) == 0


def test_tax_amount_accepts_rate_as_string():
    assert tax_amount(10000, "8.25") == 825


def test_apply_discount_on_whole_cent_results():
    assert apply_discount(2000, 10) == 200
    assert apply_discount(10000, 8) == 800
    assert apply_discount(5000, 0) == 0


def test_invoice_totals_with_discount_and_tax():
    totals = invoice_totals(10000, 8, 10)

    assert totals == {
        "subtotal": 10000,
        "discount": 1000,
        "taxable": 9000,
        "tax": 720,
        "total": 9720,
    }


def test_invoice_totals_without_discount():
    totals = invoice_totals(5000, 10)

    assert totals == {
        "subtotal": 5000,
        "discount": 0,
        "taxable": 5000,
        "tax": 500,
        "total": 5500,
    }


def test_invoice_totals_are_all_ints():
    totals = invoice_totals(12345, "8.25", 5)

    assert all(isinstance(totals[key], int) for key in totals)


def test_invoice_totals_total_is_taxable_plus_tax():
    totals = invoice_totals(9999, "7.5", "12.5")

    assert totals["total"] == totals["taxable"] + totals["tax"]
    assert totals["taxable"] == totals["subtotal"] - totals["discount"]


@pytest.mark.parametrize("rate", [8.25, "8.25", Decimal("8.25"), 8])
def test_parse_rate_percent_normalises_input(rate):
    assert parse_rate_percent(rate) == Decimal(str(rate))


def test_parse_rate_percent_rejects_negative_and_invalid_values():
    for bad_rate in (-1, "-0.5", "not a rate", "NaN", None, True):
        with pytest.raises(ValueError):
            parse_rate_percent(bad_rate)
