"""
Location: pact-plugin/tests/visualizer_node.py
Summary: The node gate shared by the visualizer's node-run JS checks
         (test_visualizer_js_pins.py, test_visualizer_assets.py). Without node
         the checks skip on a developer machine and fail under CI, because a
         CI runner image that lost node would otherwise retire them silently:
         a skip reads as green.
Used by: test_visualizer_js_pins.py (which also pins this gate),
         test_visualizer_assets.py.
"""
from __future__ import annotations

import os
import shutil

import pytest

# Values that mean "NOT CI" even though the variable is set. A bare truthiness
# check reads every one of these as CI, because a non-empty string is truthy.
_FALSY_VALUES = frozenset({"", "0", "false", "no"})
_CI_VARIABLES = ("CI", "GITHUB_ACTIONS")


def ci_is_declared() -> bool:
    """True when CI or GITHUB_ACTIONS holds a value other than a falsy spelling.

    Absent, empty, `0`, `false` and `no` mean NOT CI, in any letter case and
    ignoring surrounding whitespace.
    """
    return any(
        os.environ.get(name, "").strip().lower() not in _FALSY_VALUES
        for name in _CI_VARIABLES
    )


def require_node(what: str) -> str:
    """Return the node executable; skip without it locally, fail under CI.

    `what` names the checks that need node, for the skip or failure message.
    """
    node = shutil.which("node")
    if node is not None:
        return node
    if ci_is_declared():
        pytest.fail(f"node is not on PATH under CI; {what} must run there")
    pytest.skip(f"node executable not found on PATH; {what} skipped")
