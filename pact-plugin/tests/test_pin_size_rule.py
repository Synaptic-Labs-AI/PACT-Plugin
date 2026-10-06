"""
Location: pact-plugin/tests/test_pin_size_rule.py
Summary: The per-pin size rule, pin_caps.size_violation, through the pin-cap
         decision: every fixed size row gets its verdict, the rule's copy with
         every part on decides as the shipped rule, and each part has a mutant
         that flips the row it exists for.
Used by: pytest. The rows live in tests/fixtures/pin_growth/size_rows.py and
         the switchable copy in tests/fixtures/pin_growth/size_rule.py.
"""

import pytest

from fixtures.pin_growth import rows as R
from fixtures.pin_growth.size_rows import SIZE_ROWS
from fixtures.pin_growth.size_rule import size_rule
from pin_caps import compute_deny_reason
from shared import pin_growth

ROWS = {row.name: row for row in SIZE_ROWS}


def _verdict(row):
    decision = pin_growth.pin_cap_decision(row.before, row.after, use_timer=False)
    assert decision.cause in (None, "size"), decision
    return decision.verdict


@pytest.mark.parametrize("row", SIZE_ROWS, ids=[row.name for row in SIZE_ROWS])
def test_each_size_row_gets_its_verdict(row):
    assert _verdict(row) == row.verdict


def test_the_copy_with_every_part_on_decides_as_the_shipped_rule(monkeypatch):
    shipped, copy, calls, differ = pin_growth.size_violation, size_rule(), [], []

    def compare(pre_pins, post_pins):
        result = shipped(pre_pins, post_pins)
        calls.append(1)
        if copy(pre_pins, post_pins) != result:
            differ.append(result)
        return result

    monkeypatch.setattr(pin_growth, "size_violation", compare)
    for row in SIZE_ROWS:
        pin_growth.pin_cap_decision(row.before, row.after, use_timer=False)
    for row in R.FIXED_ROWS + R.REVEAL_ROWS:
        pin_growth.pin_cap_decision(row.pre or "", row.post, use_timer=False)
    assert len(calls) > len(SIZE_ROWS) and differ == []


MUTANTS = [
    ("heading edges dropped", dict(heading_edges=False),
     ["rewrite an oversize pin in place while 60% of its old lines move into a small pin"]),
    ("content edges dropped, descent kept", dict(content_edges=False),
     ["shrink an oversize pin and copy what it kept into a new oversize pin with added text"]),
    ("sum check dropped", dict(sum_check=False), ["split 3400 into halves, then grow one by 1000"]),
    ("max check dropped", dict(max_check=False),
     ["move a paragraph into the larger oversize pin, which grows past the largest before"]),
    ("orphan pairing dropped", dict(orphan_pairing=False), ["rename and reword the 1800 pin, shrink to about 1650"]),
    ("exact-line links dropped", dict(line_links=False), ["move a one-word line from one oversize pin to another"]),
    ("word-pair links dropped", dict(pair_links=False), ["move the paragraph rewrapped onto one line"]),
    ("word-pair share check dropped", dict(pair_share=False),
     ["grow one oversize pin with new text while another loses a longer unrelated paragraph"]),
    ("a free pin may sit in a component that has a violator after", dict(free_needs_empty_component=False),
     ["rework an oversize pin into a new one and add a second new oversize pin"]),
    ("pairing ignores descent", dict(free_needs_no_successor=False),
     ["shrink the 1800 pin to 1000 and add a new 1700 pin in one Write"]),
    ("share lowered to 48%", dict(share=0.48), ["a new oversize pin holding 49% of its word pairs from a kept pin"]),
    ("share raised to 52%", dict(share=0.52), ["a new oversize pin holding 51% of its word pairs from a kept pin"]),
]


@pytest.mark.parametrize("name, switches, red", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_each_mutant_flips_its_row(name, switches, red, monkeypatch):
    for key in red:
        assert _verdict(ROWS[key]) == ROWS[key].verdict, f"control: {key}"
    monkeypatch.setattr(pin_growth, "size_violation", size_rule(**switches))
    for key in red:
        assert _verdict(ROWS[key]) != ROWS[key].verdict, key


def test_comparing_only_the_worst_pins_misses_the_three_growth_targets(monkeypatch):
    targets = ["new 1700 pin without override while an 1800 pin exists",
               "grow the 1600 pin to 1700 while an 1800 pin exists",
               "grow a 1400 pin to 1600 while an 1800 pin exists"]
    assert all(_verdict(ROWS[key]) == "DENY" for key in targets)
    monkeypatch.setattr(pin_growth, "size_violation", lambda pre, post: compute_deny_reason(pre, post, growth=0))
    assert all(_verdict(ROWS[key]) == "ALLOW" for key in targets)
