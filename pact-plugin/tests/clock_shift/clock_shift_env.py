"""
Location: pact-plugin/tests/clock_shift/clock_shift_env.py
Summary: Carries a running clock shift into a child environment a test built on
         purpose.
Used by: tests that launch a child with an env they construct rather than copy.

Those tests keep PYTEST_CURRENT_TEST, the real HOME and similar keys out of the
child deliberately, so without this the child runs on the real clock and the
census counts it as a miss. It copies ONLY the shift seconds, the ledger path
and the shim directory (prepended to the env's own PYTHONPATH), and returns the
env unchanged when this process is not shifted.
"""
import os
from pathlib import Path

SHIFT_ENV = "PACT_TEST_CLOCK_SHIFT_SECONDS"
LEDGER_ENV = "PACT_TEST_CLOCK_SHIFT_LEDGER"
SHIM_DIR = str(Path(__file__).resolve().parent)


def carry_clock_shift(env):
    """Return a copy of env with the clock shift carried in, or env itself when unshifted."""
    shift = os.environ.get(SHIFT_ENV)
    if not shift or float(shift) == 0:
        return env
    env = dict(env)
    env[SHIFT_ENV] = shift
    if LEDGER_ENV in os.environ:
        env[LEDGER_ENV] = os.environ[LEDGER_ENV]
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if SHIM_DIR not in parts:
        env["PYTHONPATH"] = os.pathsep.join([SHIM_DIR] + parts)
    return env
