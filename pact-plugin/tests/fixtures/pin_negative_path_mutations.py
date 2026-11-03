"""Negative fixture for tests/test_path_setup_pin.py — one sys.path mutation
per matcher leg (insert / append / extend / slice-assign / augmented assign).

This file is DELIBERATE VIOLATION EVIDENCE, not path setup:
- It is exempt from the pin's population arms via _NEGATIVE_FIXTURE, and the
  pin's self-test asserts the matcher flags each mutation line below exactly.
- Editing this file shifts line numbers and FAILS the self-test — that is the
  point: update the expected lines in the same commit as any edit here.
- pytest never collects it (no test_ prefix) and nothing imports it; all
  paths are nonexistent so even an accidental import only adds dead entries.

The expected line numbers live in
test_path_setup_pin.py::test_negative_fixture_flags_every_matcher_leg.
"""

import sys

sys.path.insert(0, "/nonexistent-pin-fixture-insert")
sys.path.append("/nonexistent-pin-fixture-append")
sys.path.extend(["/nonexistent-pin-fixture-extend"])
sys.path[0:0] = ["/nonexistent-pin-fixture-slice"]
sys.path += ["/nonexistent-pin-fixture-augassign"]

import sys as _sys_alias  # noqa: E402
from sys import path as _sys_path  # noqa: E402

# The same mutation through an alias of sys, then a from-imported path.
_sys_alias.path.append("/nonexistent-pin-fixture-alias")
_sys_path.insert(0, "/nonexistent-pin-fixture-from-import")
