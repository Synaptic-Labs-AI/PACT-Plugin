"""Worst-case performance-bound regression tests for the merge-guard global-flag
prefixes (#1001 / F1).

Root cause being pinned
-----------------------
``_GH_GLOBAL_FLAGS`` / ``_GIT_GLOBAL_FLAGS`` in
``shared/merge_guard_common.py`` were ``(?:\\S+\\s+)*`` — an unbounded greedy
walk of "any token". ``\\S`` and ``\\s`` are disjoint, so a single walk is
internally unambiguous; the quadratic is the *multi-anchor* interaction: on a
command text with many ``git``/``gh`` anchor tokens, ``re.search`` retries the
walk at every anchor and each retry greedily consumes to end-of-string looking
for the following verb (``push``/``pr``/``branch``/``api``). Per-anchor cost
O(N) × N anchors = **O(N^2)**.

The fix bounds the walk to ``(?:\\S+\\s+){0,32}`` (``_MAX_GLOBAL_FLAG_TOKENS``),
so each anchor consumes at most 32 tokens => O(32)=O(1) per anchor => the whole
scan is **linear**. The same unbounded shape lived in two inline httpie copies
in ``merge_guard_pre.py`` and was bounded identically; those are pinned here too.

Functions pinned
----------------
Both detection paths embed the prefix constants and were INDEPENDENTLY quadratic
(PREPARE probe E):

* ``is_dangerous_command``            — the read-side ``DANGEROUS_PATTERNS`` bank
  (also carries the inline httpie patterns);
* ``detect_command_operation_type``   — the shared classifier, called by BOTH
  the pre- and post-hooks.

Witness
-------
``"git x " * N`` (and ``"http x " * N`` for the httpie copies): many anchors,
**no shell separators** — the pure pathological shape that maximises the
multi-anchor retry cost. Each ``x`` is a non-verb token, so the scan runs every
prefix pattern to completion (no early dangerous-match short-circuit).

Assertion strategy (CI-robust — NOT exact-ms; design §7.2)
----------------------------------------------------------
Two mutually reinforcing assertions per case:

* **Absolute wall-clock ceiling** (PRIMARY discriminator). Bounded/linear is
  ~0.06–0.18 s at N=4000 here; a 4–5x-slower CI box stays well under the
  ceiling. The unbounded/quadratic form is ~1.9 s (httpie) to ~6 s (git) at
  N=4000. The ceiling sits an order of magnitude clear of linear and below
  quadratic, so it cannot flap on a slow machine yet still trips on a
  regression.
* **Scaling ratio** ``t(2N)/t(N) < 3.0`` across one doubling. Linear ≈ 2.0,
  quadratic ≈ 4.0; 3.0 is the midpoint. ``best-of-K`` minimum timing suppresses
  upward scheduler/GC noise (a slow sample never lowers the min).

Counter-test-by-revert (non-vacuity)
------------------------------------
Restore the unbounded ``*`` form (shared constants for the git/detect cases; the
inline httpie literals for the httpie case) and re-run: the ratio returns to
~4x AND the N=4000 wall-clock blows past the ceiling => the targeted case goes
RED. Expected cardinality: reverting the shared constants reds the two git/detect
cases; reverting the httpie inline literals reds the httpie case.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from merge_guard_pre import is_dangerous_command  # noqa: E402
from shared.merge_guard_common import detect_command_operation_type  # noqa: E402

# N_LARGE = 2 * N_SMALL — the scaling-ratio doubling. N is large enough that the
# unbounded quadratic is unmistakable (~6 s for the git bank) yet the bounded
# form stays well under ~0.2 s, so the test runs in a few seconds.
N_SMALL = 2000
N_LARGE = 4000

# best-of-K minimum: the dominant timing noise is upward (scheduler preemption,
# GC), and a minimum is a clean lower bound on the true cost that a slow sample
# cannot inflate. K=5 gives five chances at a clean large-N measurement, which
# is what keeps the small-absolute-time httpie ratio from flaking.
_K = 5

# Linear ~2.0x per doubling; quadratic ~4.0x. 3.0 is the midpoint.
RATIO_CEILING = 3.0

# Per-case absolute ceilings. The git/detect bank's quadratic is ~6 s at N=4000,
# so 2.0 s is generous yet trips hard on regression. The httpie copies' quadratic
# is only ~1.9 s at N=4000 (two patterns, not ~21), so it gets a tighter 1.0 s
# ceiling — still ~15x the bounded ~0.06 s, comfortably above any slow-CI linear
# run and below the ~1.9 s quadratic.
_CEIL_GIT = 2.0
_CEIL_HTTPIE = 1.0


def _best_time(fn, arg, k=_K):
    """Minimum wall-clock of ``fn(arg)`` over k runs (suppresses upward noise)."""
    best = float("inf")
    for _ in range(k):
        t0 = time.perf_counter()
        fn(arg)
        dt = time.perf_counter() - t0
        if dt < best:
            best = dt
    return best


def _measure_scaling(fn, token):
    """Return (t_small, t_large) best-of-K times on the witness ``token * N``."""
    # Warm up once (regex objects are compiled at import; this primes caches).
    fn(token * 100)
    t_small = _best_time(fn, token * N_SMALL)
    t_large = _best_time(fn, token * N_LARGE)
    return t_small, t_large


# (id, function, witness token, absolute-ceiling-seconds)
_CASES = [
    ("is_dangerous_command/git", is_dangerous_command, "git x ", _CEIL_GIT),
    ("detect_command_operation_type/git", detect_command_operation_type, "git x ", _CEIL_GIT),
    ("is_dangerous_command/httpie", is_dangerous_command, "http x ", _CEIL_HTTPIE),
]


@pytest.mark.parametrize(
    "fn,token,abs_ceiling",
    [(fn, token, ceil) for (_id, fn, token, ceil) in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_global_flag_prefix_scaling_is_subquadratic(fn, token, abs_ceiling):
    """The bounded global-flag prefixes must scale sub-quadratically on the
    many-anchor witness through both detection paths and the inline httpie
    copies. Restoring the unbounded ``*`` form makes the ratio ~4x and the
    N=4000 wall-clock exceed the ceiling => RED."""
    t_small, t_large = _measure_scaling(fn, token)
    ratio = (t_large / t_small) if t_small > 0 else float("inf")

    # PRIMARY: generous absolute wall-clock ceiling (flake-resistant).
    assert t_large < abs_ceiling, (
        f"{fn.__name__} on {token!r}*{N_LARGE}: {t_large * 1000:.1f} ms exceeds "
        f"{abs_ceiling * 1000:.0f} ms ceiling — unbounded O(n^2) backtracking regression?"
    )

    # REINFORCING: scaling ratio across one doubling (linear ~2.0, quadratic ~4.0).
    assert ratio < RATIO_CEILING, (
        f"{fn.__name__} on {token!r}: t({N_LARGE})/t({N_SMALL}) = {ratio:.2f} "
        f">= {RATIO_CEILING} — quadratic scaling regression "
        f"(t_small={t_small * 1000:.1f} ms, t_large={t_large * 1000:.1f} ms)?"
    )
