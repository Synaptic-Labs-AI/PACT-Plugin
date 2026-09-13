"""
Location: pact-plugin/tests/clock_shift/sitecustomize.py
Summary: Loads the clock-shift shim at interpreter start in every process whose
         PYTHONPATH carries this directory.
Used by: the clock-shift sweep; clock_shift_shim.py carries the command.

Python imports only the FIRST sitecustomize on sys.path. A test that puts its own
sitecustomize ahead of this one must import clock_shift_shim itself, or its
children run on the real clock. The census fails a sweep in which one does not.
"""
import clock_shift_shim  # noqa: F401
