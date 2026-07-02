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
scan is **linear**.

Functions pinned
----------------
Both detection paths embed the prefix constants and were INDEPENDENTLY quadratic
(PREPARE probe E):

* ``is_dangerous_command``            — the read-side ``DANGEROUS_PATTERNS`` bank;
* ``detect_command_operation_type``   — the shared classifier, called by BOTH
  the pre- and post-hooks.

Witness
-------
``"git x " * N``: many anchors,
**no shell separators** — the pure pathological shape that maximises the
multi-anchor retry cost. Each ``x`` is a non-verb token, so the scan runs every
prefix pattern to completion (no early dangerous-match short-circuit).

Assertion strategy (CI-robust — NOT exact-ms; design §7.2)
----------------------------------------------------------
Two mutually reinforcing assertions per case:

* **Absolute wall-clock ceiling** (PRIMARY discriminator). Bounded/linear is
  ~0.06–0.18 s at N=4000 here; a 4–5x-slower CI box stays well under the
  ceiling. The unbounded/quadratic form is ~1.8 s (gh detect) to ~7.3 s (gh
  read) at N=4000. The ceiling sits an order of magnitude clear of linear and below
  quadratic, so it cannot flap on a slow machine yet still trips on a
  regression.
* **Scaling ratio** ``t(2N)/t(N) < 3.0`` across one doubling. Linear ≈ 2.0,
  quadratic ≈ 4.0; 3.0 is the midpoint. ``best-of-K`` minimum timing suppresses
  upward scheduler/GC noise (a slow sample never lowers the min).

Counter-test-by-revert (non-vacuity)
------------------------------------
Restore the unbounded ``*`` form (the shared prefix constants) and re-run: the
ratio returns to ~4x AND the N=4000 wall-clock blows past the ceiling => the
targeted case goes RED. Expected cardinality: reverting the shared constants
(``_GIT_GLOBAL_FLAGS`` / ``_GH_GLOBAL_FLAGS``) reds the corresponding git / gh
cases.
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
# is what keeps the small-absolute-time ratios (gh detect, push-flag walk) from
# flaking.
_K = 5

# Linear ~2.0x per doubling; quadratic ~4.0x. 3.0 is the midpoint.
RATIO_CEILING = 3.0

# Per-case absolute ceilings. The git/detect bank's quadratic is ~6 s at N=4000,
# so 2.0 s is generous yet trips hard on regression.
_CEIL_GIT = 2.0


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


# gh multi-anchor witness (review m-3 — the `_GH_GLOBAL_FLAGS` bound had no direct
# perf witness). Measured quadratic counter-factual (revert `_GH_GLOBAL_FLAGS`
# `{0,K}`->`*`): is_dangerous ~7.3 s at N=4000 (2.0 s ceiling fine), but
# detect_command_operation_type only ~1.8 s (two gh-prefix classifier patterns,
# not the ~21-pattern read bank) — so detect/gh gets a TIGHTER 1.0 s ceiling
# (per-surface ceiling calibration). Bounded: ~0.08 s / ~0.02 s.
_CEIL_GH_READ = 2.0
_CEIL_GH_DETECT = 1.0

# Push-flag-walk witness (`git push ` + `-x ` * N). The push-dash-flag walk is a
# SINGLE-anchor walk (one `git`/`push`), so even unbounded it is O(N) LINEAR, not
# quadratic — it was already linear at HEAD (the F1 outer `_GIT_PREFIX` bound caps
# the multi-anchor interaction; design §12.2). This case PINS structural linearity
# (ratio < 3.0): a future nested-quantifier regression in the push patterns would
# trip it. It is GREEN-stays-GREEN — reverting the `{0,32}` push bound keeps it
# linear, so there is NO perf counter-test-RED for it; the bound's non-vacuity is
# the >K-RESIDUAL FLIP in TestFlagTokenBoundary, not a perf revert. Bounded ~6 ms.
_CEIL_PUSHWALK = 1.0


def _measure_scaling(fn, build):
    """Return (t_small, t_large) best-of-K times on the witness ``build(N)``."""
    # Warm up once (regex objects are compiled at import; this primes caches).
    fn(build(100))
    t_small = _best_time(fn, build(N_SMALL))
    t_large = _best_time(fn, build(N_LARGE))
    return t_small, t_large


# (id, function, witness builder build(N)->str, absolute-ceiling-seconds)
_CASES = [
    ("is_dangerous_command/git", is_dangerous_command, lambda n: "git x " * n, _CEIL_GIT),
    ("detect_command_operation_type/git", detect_command_operation_type, lambda n: "git x " * n, _CEIL_GIT),
    # --- remediation additions (PR #1003) ---
    ("is_dangerous_command/gh", is_dangerous_command, lambda n: "gh x " * n, _CEIL_GH_READ),
    ("detect_command_operation_type/gh", detect_command_operation_type, lambda n: "gh x " * n, _CEIL_GH_DETECT),
    ("is_dangerous_command/push-flag-walk", is_dangerous_command, lambda n: "git push " + "-x " * n, _CEIL_PUSHWALK),
    ("detect_command_operation_type/push-flag-walk", detect_command_operation_type, lambda n: "git push " + "-x " * n, _CEIL_PUSHWALK),
]


@pytest.mark.parametrize(
    "fn,build,abs_ceiling",
    [(fn, build, ceil) for (_id, fn, build, ceil) in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_global_flag_prefix_scaling_is_subquadratic(fn, build, abs_ceiling):
    """The bounded global-flag prefixes / push-flag walk must scale
    sub-quadratically on the worst-case witness through both detection paths,
    the gh prefix (review m-3), and the push-flag walk
    (structural-linearity pin, §12.2). Restoring an unbounded `*` form on a
    MULTI-anchor witness (git/gh) makes the ratio ~4x and the N=4000
    wall-clock exceed the ceiling => RED. The push-flag-walk witness is
    single-anchor (already linear, defense-in-depth) so it stays GREEN under
    revert — its non-vacuity lives in TestFlagTokenBoundary's >K-residual flip."""
    t_small, t_large = _measure_scaling(fn, build)
    ratio = (t_large / t_small) if t_small > 0 else float("inf")
    witness = build(1)

    # PRIMARY: generous absolute wall-clock ceiling (flake-resistant).
    assert t_large < abs_ceiling, (
        f"{fn.__name__} on {witness!r}*{N_LARGE}: {t_large * 1000:.1f} ms exceeds "
        f"{abs_ceiling * 1000:.0f} ms ceiling — unbounded O(n^2) backtracking regression?"
    )

    # REINFORCING: scaling ratio across one doubling (linear ~2.0, quadratic ~4.0).
    assert ratio < RATIO_CEILING, (
        f"{fn.__name__} on {witness!r}: t({N_LARGE})/t({N_SMALL}) = {ratio:.2f} "
        f">= {RATIO_CEILING} — quadratic scaling regression "
        f"(t_small={t_small * 1000:.1f} ms, t_large={t_large * 1000:.1f} ms)?"
    )


# ---------------------------------------------------------------------------
# Flag-token bound — FUNCTIONAL boundary + the accepted >K residual (#1001 /
# remediation §12.2-12.3). The same `_MAX_GLOBAL_FLAG_TOKENS` (=32) that bounds
# the scaling above also bounds the push-dash-flag walk between `push` and its
# refspec. These tests pin the K=32 boundary and the accepted >K residual
# under-block, and document why the push-walk bound's non-vacuity is the
# >K-residual FLIP (not a perf revert).
# ---------------------------------------------------------------------------

from shared.merge_guard_common import _MAX_GLOBAL_FLAG_TOKENS  # noqa: E402


class TestFlagTokenBoundary:
    """K=32 push-flag-walk boundary + accepted >K residual under-block."""

    def test_within_bound_push_to_main_is_detected(self):
        """A push-to-main with EXACTLY _MAX_GLOBAL_FLAG_TOKENS (32) dash-flags is
        still within the bound, so the refspec is reachable and it IS detected
        (push-to-main class) on both the read bank and the classifier. (Empirical
        boundary: -x*32 detected; -x*33 the first missed — the residual below.)"""
        cmd = "git push " + "-x " * _MAX_GLOBAL_FLAG_TOKENS + "origin main"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) == "push-to-main"

    def test_past_bound_push_to_main_is_the_accepted_residual(self):
        """ACCEPTED >K RESIDUAL (documented, INV-D2 relaxation — §12.3): a
        push-to-main padded with MORE than _MAX_GLOBAL_FLAG_TOKENS dash-flags
        exceeds the bound, the refspec becomes unreachable, and it is NOT detected.
        This is the deliberate, threat-model-justified tradeoff vs the O(n^2) DoS
        (an operator padding 33+ no-op flags to evade their OWN guard is
        self-defeating). Pinning it makes the residual VISIBLE: un-bounding the
        walk (`{0,K}`->`*`) flips this to detected — which is the push-walk bound's
        non-vacuity witness (see this test's counter-test in the remediation HANDOFF)."""
        cmd = "git push " + "-x " * (_MAX_GLOBAL_FLAG_TOKENS + 1) + "origin main"
        assert is_dangerous_command(cmd) is False
        assert detect_command_operation_type(cmd) is None

    def test_realistic_flag_count_push_to_main_is_detected(self):
        """A realistic push-to-main with a handful of dash-flags (well within K)
        is detected — the bound does not perturb any realistic command."""
        cmd = "git push -u -v --no-verify origin main"
        assert is_dangerous_command(cmd) is True
        assert detect_command_operation_type(cmd) == "push-to-main"


# ---------------------------------------------------------------------------
# Privileged-flag scanner linearity (#1042 INV-D2). extract_privileged_flags is
# a single str.split() token-walk with constant per-token work (the op-class
# denylist has <=3 entries; the git-surface abbreviation scan compares a token
# against a <=1-entry long-name list) — O(n) LINEAR, no regex / no backtracking.
# Like the push-flag-walk case above it is structurally linear with NO quadratic
# predecessor to revert to (GREEN-stays-GREEN — no perf counter-test-RED); its
# guard is the ratio/ceiling, which a future nested-quantifier or per-token
# rescan regression would trip.
# ---------------------------------------------------------------------------

from shared.merge_guard_common import extract_privileged_flags  # noqa: E402


class TestPrivilegedFlagScannerLinearity:
    """The #1042 bound-flag scanner scales sub-quadratically on a worst-case
    many-token witness, through both the short-cluster walk (merge surface) and
    the git-surface prefix-abbreviation scan (force-push surface)."""

    @pytest.mark.parametrize("op,build", [
        # merge surface: single-dash cluster walk over unbound shorts (per-token
        # constant work; no value-consumption short-circuit to confound the shape).
        ("merge", lambda n: "-x " * n),
        # force-push surface: every token enters the unambiguous-prefix expansion
        # scan against the <=1-entry long-name list (the abbreviation branch).
        ("force-push", lambda n: "--n " * n),
    ], ids=["merge_short_cluster_walk", "force_push_abbreviation_scan"])
    def test_scanner_scales_linearly(self, op, build):
        fn = lambda cmd: extract_privileged_flags(cmd, op)  # noqa: E731
        t_small, t_large = _measure_scaling(fn, build)
        ratio = (t_large / t_small) if t_small > 0 else float("inf")

        # PRIMARY: generous absolute ceiling (linear is sub-ms here even at N=4000).
        assert t_large < 1.0, (
            f"extract_privileged_flags({op}) on {build(1)!r}*{N_LARGE}: "
            f"{t_large * 1000:.1f} ms exceeds 1000 ms ceiling — backtracking regression?"
        )
        # REINFORCING: scaling ratio across one doubling (linear ~2.0, quadratic ~4.0).
        assert ratio < RATIO_CEILING, (
            f"extract_privileged_flags({op}) on {build(1)!r}: "
            f"t({N_LARGE})/t({N_SMALL}) = {ratio:.2f} >= {RATIO_CEILING} — "
            f"quadratic scaling regression (t_small={t_small * 1000:.1f} ms, "
            f"t_large={t_large * 1000:.1f} ms)?"
        )
