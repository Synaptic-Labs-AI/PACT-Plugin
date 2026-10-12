"""
Location: pact-plugin/tests/test_pin_growth_ci_timing.py
Summary: The worst-case pin-cap decision, timed on the CI runners. Runs only
         when CI is set.
Used by: pytest.

The costliest known shape, a memory block of 4,000 lines in which every other
line repeats and the lines between differ, is decided by the gate's own
decision with the interrupting timer armed, as the hook runs it. The test
asserts the outcome and never the time, so a slow runner cannot fail it: the
change is allowed with the size note, and a step budget stops it, the growth
rule's within one row of its limit, never the timer. Every part of the
decision counts its work in a budget named for the part, and the parts that
ran before the one that stopped finished within their own. The elapsed
seconds, each part's steps against its limit and the gate's note, which names
the part that stopped, are reported as a warning, so a passing CI run shows
them in its warnings summary.
"""

import os
import platform
import time
import warnings

import pytest

from shared import pin_growth
from test_pin_growth import NEW, _alternating, _SpyBudget, pin, sub

LINES = 4000


class WorstCaseTiming(UserWarning):
    """The measured worst-case decision time, reported on a passing run."""


@pytest.mark.skipif(not os.environ.get("CI"), reason="the worst-case gate timing runs when CI is set")
def test_the_costliest_shape_is_allowed_with_the_size_note_within_the_step_budget(monkeypatch):
    from pin_caps_gate import gate_decision

    _SpyBudget.made = []
    monkeypatch.setattr(pin_growth, "_Budget", _SpyBudget)
    before = _alternating(LINES, False)
    after = sub(_alternating(LINES, True), pin(13), pin(13) + NEW)

    started = time.perf_counter()
    decision = gate_decision(before, "Write", {"content": after})
    elapsed = time.perf_counter() - started

    parts = ", ".join(f"{budget.part} {budget.spent:,} of {budget.limit:,} steps" for budget in _SpyBudget.made)
    warnings.warn(WorstCaseTiming(
        f"worst-case pin-cap decision on Python {platform.python_version()}: {elapsed:.2f} s, "
        f"{parts}, {LINES:,} lines, {len(before):,} characters before the change. "
        f"The gate said: {decision.reason}"))

    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound"), decision
    # A budget stopped the decision, never the timer, and the note names its part.
    assert decision.reason == ("The pin cap check stopped early and allowed this change: "
                               f"{pin_growth.GROWTH_RULE} passed its step budget of {pin_growth.STEP_BUDGET}."), decision
    *finished, growth = _SpyBudget.made
    assert growth.part == pin_growth.GROWTH_RULE
    # One outer row spends one step plus one per occurrence of its line on the
    # other side, so the budget stops the rule at most one row past its limit.
    assert growth.limit < growth.spent <= growth.limit + 1 + after.count("\nx\n") + 1
    # The parts before it finished within their own budgets, so the rule ran.
    assert all(budget.spent <= budget.limit for budget in finished), parts
