"""The `requires_non_root` marker's decision, kept out of conftest so it can be
pinned directly (tests/test_requires_non_root_marker.py).

WHY THE MARKER EXISTS. A test that provokes an access failure with mode bits
(`chmod 0o000` on a file, `chmod 0o500` on a directory) measures nothing when
the suite runs as uid 0: root bypasses discretionary access checks, the
expected PermissionError never fires, and the test fails for a reason that has
nothing to do with the code under test. Containers and some CI images run as
root, so this is a real environment, not a hypothetical.

WHY IT SKIPS, unlike the dependency markers registered in conftest. Those
markers declare something a constrained environment may lack and leave the
choice to deselect with the operator. Here there is no choice to leave: under
root the precondition the test builds cannot exist, so running the test only
reports a false red. The skip reason names the cause, so it reads as an
environmental exclusion in `-rs` output rather than a quietly disabled test.

PREFER NOT TO USE IT. A test that only needs "the read or write raises
OSError" can usually build that failure without mode bits (a directory where a
file is expected, a file where a directory is expected), which keeps the test
running under root. Apply the marker only when the test's subject IS the
permission failure, or when the failure has to land at one specific step that
no mode-free fault can reach.

Per the thin-conftest rule this is a direct-import helper; conftest wires it
into `pytest_runtest_setup`.
"""

import os
from typing import Optional

MARKER = "requires_non_root"

REASON = (
    "requires_non_root: running as uid 0, which bypasses file mode bits, so "
    "the permission failure this test provokes with chmod cannot occur"
)


def non_root_skip_reason() -> Optional[str]:
    """Return the skip reason when the process runs as root, else None.

    `os.geteuid` is looked up at call time, so a test can monkeypatch it. Where
    it does not exist (Windows), mode bits are not the access model at all and
    the marker does nothing; those tests already guard their own platform.
    """
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        return REASON
    return None
