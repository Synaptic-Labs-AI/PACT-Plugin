"""End-to-end invoice totals that expose the rounding bug."""

import pytest

from invoice import invoice_totals


def test_totals_round_discount_and_tax_half_cents_up():
    totals = invoice_totals(1005, 10, 10)

    assert totals == {
        "subtotal": 1005,
        "discount": 101,
        "taxable": 904,
        "tax": 90,
        "total": 994,
    }


def test_totals_round_half_cent_tax_up_without_discount():
    totals = invoice_totals(1010, 5)

    assert totals == {
        "subtotal": 1010,
        "discount": 0,
        "taxable": 1010,
        "tax": 51,
        "total": 1061,
    }


def test_totals_round_fractional_discount_up():
    totals = invoice_totals(2010, 5, 5)

    assert totals["discount"] == 101
    assert totals["taxable"] == 1909
    assert totals["tax"] == 95
    assert totals["total"] == 2004


def test_tax_is_charged_on_the_taxable_amount_after_discount():
    # A naive implementation that taxes the full subtotal would report 800.
    assert invoice_totals(10000, 8, 25)["tax"] == 600


@pytest.mark.parametrize(
    ("subtotal", "rate", "discount"),
    [
        (1005, 10, 10),
        (1010, 5, 0),
        (2010, 5, 5),
        (9999, 8.25, 0),
        (10000, 8, 25),
        (1, 5, 0),
        (0, 8.25, 0),
    ],
)
def test_totals_breakdown_is_internally_consistent(subtotal, rate, discount):
    totals = invoice_totals(subtotal, rate, discount)

    assert set(totals) == {"subtotal", "discount", "taxable", "tax", "total"}
    assert all(isinstance(value, int) for value in totals.values())
    assert totals["subtotal"] == subtotal
    assert totals["taxable"] == totals["subtotal"] - totals["discount"]
    assert totals["total"] == totals["taxable"] + totals["tax"]


def test_exact_cent_invoices_are_unchanged():
    assert invoice_totals(10000, 8, 10) == {
        "subtotal": 10000,
        "discount": 1000,
        "taxable": 9000,
        "tax": 720,
        "total": 9720,
    }
    assert invoice_totals(5000, 10) == {
        "subtotal": 5000,
        "discount": 0,
        "taxable": 5000,
        "tax": 500,
        "total": 5500,
    }
