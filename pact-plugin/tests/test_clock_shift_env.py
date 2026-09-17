"""
Location: pact-plugin/tests/test_clock_shift_env.py
Summary: Pins carry_clock_shift, which carries only the three clock-shift values
         into a built child env and changes nothing when the run is not shifted.
Used by: the suite.
"""
import os

from clock_shift.clock_shift_env import LEDGER_ENV, SHIFT_ENV, SHIM_DIR, carry_clock_shift


def test_an_unshifted_run_returns_the_env_untouched(monkeypatch):
    """GUARD. MUTANT: carry the values whether or not the run is shifted."""
    monkeypatch.delenv(SHIFT_ENV, raising=False)
    env = {"PATH": "/usr/bin"}
    assert (carry_clock_shift(env) is env, env) == (True, {"PATH": "/usr/bin"})


def test_a_zero_shift_counts_as_unshifted(monkeypatch):
    monkeypatch.setenv(SHIFT_ENV, "0")
    env = {"PATH": "/usr/bin"}
    assert carry_clock_shift(env) is env


def test_a_shifted_run_carries_exactly_the_three_values(monkeypatch):
    """MUTANTS: copy os.environ wholesale; drop the ledger; append the shim
    directory instead of prepending it; update the caller's dict in place."""
    monkeypatch.setenv(SHIFT_ENV, "86400")
    monkeypatch.setenv(LEDGER_ENV, "/tmp/ledger")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x.py::t (call)")
    built = {"PATH": "/usr/bin", "PYTHONPATH": "/built/hooks"}
    carried = carry_clock_shift(built)
    assert (carried, built) == (
        {
            "PATH": "/usr/bin",
            "PYTHONPATH": os.pathsep.join([SHIM_DIR, "/built/hooks"]),
            SHIFT_ENV: "86400",
            LEDGER_ENV: "/tmp/ledger",
        },
        {"PATH": "/usr/bin", "PYTHONPATH": "/built/hooks"},
    )
