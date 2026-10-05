"""
Location: pact-plugin/tests/test_pin_growth_ci_timing.py
Summary: The worst-case pin-cap decision, timed on the CI runners. Runs only
         when CI is set.
Used by: pytest.

The costliest known shape, a memory block of 4,000 lines in which every other
line repeats and the lines between differ, is decided by the gate's own
decision with the interrupting timer armed, as the hook runs it. The test
asserts the outcome and never the time, so a slow runner cannot fail it: the
change is allowed with the size note, and the rule stops within one row of its
step budget, or earlier if the timer stops it first. The elapsed seconds, the
steps spent and the gate's note, which names the bound that stopped the rule,
are reported as a warning, so a passing CI run shows them in its warnings
summary.
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

    (spy,) = _SpyBudget.made
    # The gate's own note names the bound that stopped the rule.
    warnings.warn(WorstCaseTiming(
        f"worst-case pin-cap decision on Python {platform.python_version()}: {elapsed:.2f} s, "
        f"{spy.spent:,} steps of a {spy.limit:,}-step budget, {LINES:,} lines, "
        f"{len(before):,} characters before the change. The gate said: {decision.reason}"))

    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound"), decision
    assert decision.reason
    # One outer row spends one step plus one per occurrence of its line on the
    # other side, so the budget stops the rule at most one row past its limit.
    assert spy.spent <= spy.limit + 1 + after.count("\nx\n") + 1
