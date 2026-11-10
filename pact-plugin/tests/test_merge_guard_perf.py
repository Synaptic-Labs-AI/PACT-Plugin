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

Assertion strategy (scaling plus normalized absolute cost)
-------------------------------------------------------------
The four multi-anchor cases measure three sizes and require both doubling
ratios to stay below 3.0. Linear work is ~2.0x per doubling; quadratic work
is ~4.0x. Those ratios cannot detect a constant-factor slowdown, so the
largest measurement is also divided by a same-process known-linear reference:
``str.split`` on the same witness. The normalized absolute budget catches a
linear 4x regression without restoring a machine-specific millisecond bound.
Every timing, ratio, reference, normalized value, and threshold is printed
before the assertions.

TIMED ON CPU TIME (``time.process_time``), NOT WALL CLOCK. Wall clock also
counts time waiting for a core, which concurrent suites needlessly include.
Normalized cost uses the best-of-K per-pair minimum to suppress upward
scheduler/GC noise. Raw scaling ratios use the median target at each size, so
one anomalously fast size cannot invert a linear doubling into a false quadratic.

The push-flag-walk cases retain their bounded absolute CPU-time ceiling.
They are single-anchor linear witnesses, so restoring the global-prefix bound
does not produce a quadratic counterfactual for them.

Counter-test-by-revert (non-vacuity)
------------------------------------
Restore the unbounded ``*`` form in either shared prefix constant: both
corresponding git or gh cases go red on the doubling ratios and normalized
absolute budget, while the normal bounded controls stay green. A 4x linear
workload preserves the raw doubling ratios but reds the normalized budget.
These are manual counter-tests run against a modified copy; no arm in this
module runs them. The measured values are beside ``NORMALIZED_ABSOLUTE_CEILING``.
"""

import time
from pathlib import Path
from statistics import median

import pytest

from merge_guard_pre import is_dangerous_command  # noqa: E402
from shared.merge_guard_common import detect_command_operation_type  # noqa: E402

# The multi-anchor cases measure all three sizes; the push-flag-walk cases
# measure N_LARGE only; the privileged-flag scanner linearity cases measure the
# N_SMALL/N_LARGE pair.
N_SMALL = 4000
N_LARGE = 8000

# A ratio's noise grows as its denominator shrinks: an arm of ~33 ms once read
# 3.85 against the 3.0 ceiling under load. At 2000 tokens every multi-anchor arm
# measures ~130-180 ms of CPU time, and quiet or under three concurrent
# four-worker runs the 2000->4000 ratio stayed within 1.96-2.06 (aarch64).
# If a faster runner brings this arm down towards tens of milliseconds, raise
# the sizes, not the ratio ceiling.
N_MULTI_SMALL = 2000

# best-of-K minimum: scheduler preemption and GC add upward noise, and a slow
# sample cannot inflate the minimum.
_K = 5

# Linear ~2.0x per doubling; quadratic ~4.0x. 3.0 is the midpoint.
RATIO_CEILING = 3.0

# Normalized target/reference cost at N_LARGE, one ceiling for all four
# multi-anchor cases. Measured on aarch64, quiet and under three concurrent
# four-worker runs: git cases 2020-2160, gh cases 1520-1730. On x86_64 runners
# these values are bounded only by green CI runs, which show they stay below
# the ceiling but not by how much. The ceiling therefore detects a linear
# constant-factor slowdown of about 1.9x on the git cases and 2.3x-2.6x on the
# gh cases. Measured counter-tests: a 4x slowdown of is_dangerous_command gave
# 8610-8720 (git) and 6500-6640 (gh); an unbounded gh prefix gave over 200000.
NORMALIZED_ABSOLUTE_CEILING = 4000.0


def _best_time(fn, arg, k=_K):
    """Minimum CPU time of ``fn(arg)`` over k runs (suppresses upward noise)."""
    best = float("inf")
    for _ in range(k):
        t0 = time.process_time()
        fn(arg)
        dt = time.process_time() - t0
        if dt < best:
            best = dt
    return best


def _best_paired_times(fn, arg, k=_K, reference_fn=str.split, target_aggregate=min):
    """Aggregate target, best reference, and best per-pair normalized cost."""
    targets = []
    best_reference = best_normalized = float("inf")
    for _ in range(k):
        t0 = time.process_time()
        fn(arg)
        target = time.process_time() - t0
        t0 = time.process_time()
        reference_fn(arg)
        reference = time.process_time() - t0
        targets.append(target)
        best_reference = min(best_reference, reference)
        best_normalized = min(best_normalized, target / reference)
    return target_aggregate(targets), best_reference, best_normalized


def _measure_paired_scaling(fn, build, sizes):
    """Measure target/reference adjacently at each size, avoiding block bias."""
    fn(build(100))
    return tuple(_best_paired_times(fn, build(n), target_aggregate=median) for n in sizes)


def test_measure_paired_scaling_uses_paired_median_samples(monkeypatch):
    """The PRODUCTION sampler, not only the helper, must pair and take the median.

    Reverting ``_measure_paired_scaling`` to separate target/reference blocks,
    or its median aggregate to the minimum, changes the returned tuple and
    reddens this arm. The clock is synthetic, so the result does not depend on
    machine load: every ``process_time`` call advances by ``1 + tick % 5``
    milliseconds, and only the call sequence decides the measured durations.
    It pins the call sequence on purpose; a refactor that changes how many
    times the clock is read must re-derive these three numbers.
    """
    now = 0.0
    tick = 0

    def process_time():
        nonlocal now, tick
        tick += 1
        now += 0.001 * (1 + tick % 5)
        return now

    def target(_arg):
        return None

    monkeypatch.setattr(time, "process_time", process_time)

    (target_cost, reference_cost, normalized), = _measure_paired_scaling(
        target, lambda n: "x " * n, sizes=(1,))

    assert target_cost == pytest.approx(0.003)
    assert reference_cost == pytest.approx(0.001)
    assert normalized == pytest.approx(1 / 3)


# Push-flag-walk is a single-anchor linear witness. Keep its absolute CPU-time
# ceiling; restoring an unbounded global prefix does not make this case
# quadratic, so its non-vacuity lives in TestFlagTokenBoundary instead.
_CEIL_PUSHWALK = 1.0

# `_VALUE_TOKEN` HAS NO ARM IN THIS LADDER, AND THE ATTEMPT IS RECORDED SO IT IS
# NOT REPEATED. A ratio arm on the `$''` witness was built, sized into the
# hundreds-of-milliseconds band the sizes above are chosen to reach (254 ms / 783 ms),
# and it STILL FLAKED: measured 1.98 in isolation and 3.08 under a loaded
# full-suite run, against the 3.0 ceiling. Being in the band is NECESSARY BUT
# NOT SUFFICIENT — larger sizes fix a small-denominator problem, and this is a
# different one. Contention inflated the two arms UNEQUALLY (1.5x on the small,
# 2.4x on the large), and a ratio cannot subtract that: the asymmetry lands
# entirely in the quotient.
#
# The direction is what makes it unacceptable rather than merely annoying. A
# ratio drifting up towards 4.0 is the QUADRATIC SIGNATURE this ceiling exists
# to detect, so the false red is indistinguishable from the true one, and the
# reader who dismisses it as flaky is being trained for the day it is real.
#
# `_VALUE_TOKEN` is guarded by TestValueTokenPositionalSafety instead — a
# structural pin plus bounded absolute checks. That suits the property better
# anyway: its hazard is EXPONENTIAL, and a ratio cannot measure an exponential
# because the larger arm never returns.


def _measure_scaling(fn, build, sizes=(N_SMALL, N_LARGE)):
    """Return best-of-K CPU times for ``fn(build(N))`` at each requested size."""
    # Warm up once (regex objects are compiled at import; this primes caches).
    fn(build(100))
    return tuple(_best_time(fn, build(n)) for n in sizes)


# (id, function, witness builder build(N)->str, normalized absolute budget or
# raw seconds for absolute-only cases, ratio-ceiling or None for absolute-only)
_CASES = [
    ("is_dangerous_command/git", is_dangerous_command,
     lambda n: "git x " * n, NORMALIZED_ABSOLUTE_CEILING, RATIO_CEILING),
    ("detect_command_operation_type/git", detect_command_operation_type,
     lambda n: "git x " * n, NORMALIZED_ABSOLUTE_CEILING, RATIO_CEILING),
    ("is_dangerous_command/gh", is_dangerous_command,
     lambda n: "gh x " * n, NORMALIZED_ABSOLUTE_CEILING, RATIO_CEILING),
    ("detect_command_operation_type/gh", detect_command_operation_type,
     lambda n: "gh x " * n, NORMALIZED_ABSOLUTE_CEILING, RATIO_CEILING),
    ("is_dangerous_command/push-flag-walk", is_dangerous_command,
     lambda n: "git push " + "-x " * n, _CEIL_PUSHWALK, None),
    ("detect_command_operation_type/push-flag-walk", detect_command_operation_type,
     lambda n: "git push " + "-x " * n, _CEIL_PUSHWALK, None),
]


@pytest.mark.parametrize(
    "fn,build,abs_ceiling,ratio_ceiling",
    [(fn, build, ceil, rceil) for (_id, fn, build, ceil, rceil) in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_global_flag_prefix_scaling_is_subquadratic(fn, build, abs_ceiling, ratio_ceiling):
    """Multi-anchor prefixes scale linearly across two doublings.

    The single-anchor push-flag-walk cases retain their bounded absolute check;
    their structural non-vacuity lives in TestFlagTokenBoundary.
    """
    witness = build(1)
    if ratio_ceiling is None:
        (t_large,) = _measure_scaling(fn, build, sizes=(N_LARGE,))
        assert t_large < abs_ceiling, (
            f"{fn.__name__} on {witness!r}*{N_LARGE}: {t_large * 1000:.1f} ms exceeds "
            f"{abs_ceiling * 1000:.0f} ms ceiling — unbounded O(n^2) backtracking regression?"
        )
        return

    sizes = (N_MULTI_SMALL, N_SMALL, N_LARGE)
    paired = _measure_paired_scaling(fn, build, sizes=sizes)
    t_small, t_intermediate, t_large = tuple(row[0] for row in paired)
    reference_times = tuple(row[1] for row in paired)
    normalized = tuple(row[2] for row in paired)
    first_ratio = (t_intermediate / t_small) if t_small > 0 else float("inf")
    second_ratio = (t_large / t_intermediate) if t_intermediate > 0 else float("inf")
    print(
        f"{fn.__name__} on {witness!r}: "
        f"t_small={t_small * 1000:.1f} ms, "
        f"t_intermediate={t_intermediate * 1000:.1f} ms, "
        f"t_large={t_large * 1000:.1f} ms, "
        f"ratio_small_to_intermediate={first_ratio:.2f}, "
        f"ratio_intermediate_to_large={second_ratio:.2f}, "
        f"reference_times_ms={tuple(round(v * 1000, 3) for v in reference_times)}, "
        f"normalized={tuple(round(v, 1) for v in normalized)}, "
        f"normalized_ceiling={abs_ceiling:.1f}"
    )
    for label, ratio in (("small-to-intermediate", first_ratio),
                         ("intermediate-to-large", second_ratio)):
        assert ratio < ratio_ceiling, (
            f"{fn.__name__} on {witness!r}: {label} ratio {ratio:.2f} "
            f">= {ratio_ceiling} — quadratic scaling regression "
            f"(t_small={t_small * 1000:.1f} ms, "
            f"t_intermediate={t_intermediate * 1000:.1f} ms, "
            f"t_large={t_large * 1000:.1f} ms)?"
        )
    assert normalized[-1] < abs_ceiling, (
        f"{fn.__name__} on {witness!r}: normalized absolute cost "
        f"{normalized[-1]:.1f} >= {abs_ceiling:.1f} — constant-factor or "
        f"quadratic regression (target={t_large:.6f}s, "
        f"reference={reference_times[-1]:.6f}s)?"
    )


def test_paired_samples_remove_cross_block_normalized_bias(monkeypatch):
    """A slow target block must not be normalized by a later fast reference block."""
    now = 0.0
    factor = 1.0

    def process_time():
        return now

    def target(_):
        nonlocal now
        now += 0.001 * factor

    def reference(_):
        nonlocal now
        now += 0.0000005 * factor

    monkeypatch.setattr(time, "process_time", process_time)
    factor = 2.5
    old_target = _best_time(target, None)
    factor = 1.0
    old_reference = _best_time(reference, None)
    old_normalized = old_target / old_reference
    assert old_normalized > NORMALIZED_ABSOLUTE_CEILING

    pair = 0
    factors = (2.5, 1.0, 2.5, 1.0, 2.5)

    def paired_target(_):
        nonlocal now
        now += 0.001 * factors[pair]

    def paired_reference(_):
        nonlocal now, pair
        now += 0.0000005 * factors[pair]
        pair += 1

    now = 0.0
    new_target, new_reference, new_normalized = _best_paired_times(
        paired_target, None, reference_fn=paired_reference)
    assert new_target == pytest.approx(0.001)
    assert new_reference == pytest.approx(0.0000005)
    assert new_normalized == pytest.approx(2000.0)
    assert new_normalized < NORMALIZED_ABSOLUTE_CEILING < old_normalized


def test_median_raw_scaling_rejects_size_outlier_but_not_quadratic(monkeypatch):
    """One fast size cannot create a false quadratic; true quadratic stays red."""
    now = 0.0
    costs = {"small": [1, 2, 2, 2, 2], "intermediate": [4, 4, 4, 4, 4]}
    calls = {"small": 0, "intermediate": 0}

    def process_time():
        return now

    def target(size):
        nonlocal now
        index = calls[size]
        calls[size] += 1
        now += costs[size][index] * 0.001

    def reference(_):
        nonlocal now
        now += 0.0000005

    monkeypatch.setattr(time, "process_time", process_time)
    old_small = _best_time(target, "small")
    old_intermediate = _best_time(target, "intermediate")
    assert old_intermediate / old_small > RATIO_CEILING

    calls = {"small": 0, "intermediate": 0}
    new_small, _, _ = _best_paired_times(
        target, "small", reference_fn=reference, target_aggregate=median)
    new_intermediate, _, _ = _best_paired_times(
        target, "intermediate", reference_fn=reference, target_aggregate=median)
    assert new_intermediate / new_small == pytest.approx(2.0)
    assert new_intermediate / new_small < RATIO_CEILING

    costs = {"small": [2, 2, 2, 2, 2], "intermediate": [8, 8, 8, 8, 8]}
    calls = {"small": 0, "intermediate": 0}
    quadratic_small, _, _ = _best_paired_times(
        target, "small", reference_fn=reference, target_aggregate=median)
    quadratic_intermediate, _, _ = _best_paired_times(
        target, "intermediate", reference_fn=reference, target_aggregate=median)
    assert quadratic_intermediate / quadratic_small > RATIO_CEILING


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

        # PRIMARY: generous absolute ceiling (linear measures ~25-28 ms of CPU
        # time at N_LARGE on aarch64, so 1.0 s leaves ~35x of room).
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


# ---------------------------------------------------------------------------
# WHY THE THREE CLASSES BELOW DO NOT USE THE RATIO INSTRUMENT
#
# A ratio separates LINEAR from QUADRATIC, and it is the right instrument for
# every arm above, because a quadratic arm still TERMINATES — you get two
# numbers and divide them. The properties below are EXPONENTIAL, and a ratio
# cannot measure an exponential: computing it requires the larger arm to finish,
# and the larger arm is the one that does not. A regression would arrive as a CI
# TIMEOUT rather than a red test, which reports as infrastructure trouble rather
# than as the defect.
#
# So these use a BOUNDED input with a generous absolute ceiling — and the
# constants are justified by the complexity difference they separate, not by a
# baseline on one machine. Each is measured in BOTH states below, and the gaps
# are five orders of magnitude, so no runner is slow enough to confuse them.
# ---------------------------------------------------------------------------

import re  # noqa: E402
from pathlib import Path  # noqa: E402

from shared import merge_guard_common as _mgc  # noqa: E402
from shared.merge_guard_common import _shell_tokenize, _strip_flag_values  # noqa: E402
from merge_guard_pre import _GH_PR_NUMBER_RE  # noqa: E402


class TestValueTokenPositionalSafety:
    """`_VALUE_TOKEN` is linear because of WHERE it sits, not what it contains.

    MEASURED, in both directions, because the shape alone predicts neither.

    As shipped it is LINEAR: ~2.0x per doubling through `_strip_flag_values`,
    through `is_dangerous_command` and through `detect_command_operation_type`,
    on the worst witness available, and FLAT in the adversarial region — 0.12 ms
    at 16 units and still 0.13 ms at 28, where a reachable form costs 42 s.

    The pattern in ISOLATION is EXPONENTIAL, and worse than the `_GH_FLAG_TOKENS`
    backtracker that was fixed on this branch. Forced to fail a full match, the
    `$''` and `$""` witnesses cost roughly 16x PER ADDED UNIT — 7 ms at 16 units,
    111 ms at 20, 1.75 s at 24, and 28.7 SECONDS at 28 units, which is an
    85-CHARACTER input. The flag-token backtracker needed ~46 tokens to reach the
    same place. Controls, so the cause is not guessed: a bare `$` repetition and
    a plain-character run are FLAT (~1.2x), so the ambiguity is specifically the
    three `$`-initial arms, where a `$` can begin `$'...'`, begin `$"..."`, or
    stand alone.

    THE TWO FACTS ARE RECONCILED BY POSITION. The token ends with `+` and NOTHING
    FOLLOWS IT in the one pattern that uses it, so the greedy match always
    succeeds and no alternative partition is ever explored. Add a required
    element after it and every partition becomes reachable — the same positional
    argument that makes the quote-aware span safe, and the same one that did NOT
    hold for the flag-token walk, which had required elements after it.

    So the load-bearing guard is STRUCTURAL, not a timing ladder. A ladder pins
    the linearity; it cannot see the edit that removes the reason for it, because
    the witness that would expose the edit depends on what the edit appended.
    This class pins the precondition itself: no timing, no flake, no dependence
    on a runner, and it reddens on the exact change that makes 28 seconds
    reachable.
    """

    def _value_token_use_sites(self):
        """Non-comment lines that USE `_VALUE_TOKEN`, excluding its definition."""
        source = Path(_mgc.__file__).read_text(encoding="utf-8")
        sites = []
        for raw in source.splitlines():
            line = raw.strip()
            if "_VALUE_TOKEN" not in line:
                continue
            if line.startswith("#") or line.startswith("_VALUE_TOKEN"):
                continue          # prose, and the definition itself
            sites.append(line)
        return source, sites

    def test_value_token_is_defined_and_used_exactly_once(self):
        """Parse anchor: the scan must find the definition and one use site.

        Asserted before the positional check, and with a distinct message,
        because that check is absence-shaped — "nothing is concatenated after
        `_VALUE_TOKEN`" is satisfied perfectly by a scan that found no use sites
        at all. Ground truth independent of the scan: the token is imported and
        exercised by the arms above, so it demonstrably exists and is used.
        """
        source, sites = self._value_token_use_sites()

        assert "_VALUE_TOKEN = (" in source, (
            f"PARSE FAILURE, not a safety regression: no `_VALUE_TOKEN` "
            f"definition found in {_mgc.__file__}. The constant was renamed or "
            f"restructured. RE-POINT THIS SCAN — do not delete it; the "
            f"positional property it guards is what keeps the token linear."
        )
        assert len(sites) == 1, (
            f"PARSE FAILURE or a NEW USE SITE — either way this needs a human. "
            f"Expected exactly one line using `_VALUE_TOKEN`; found "
            f"{len(sites)}: {sites}. If a second consumer was added, its "
            f"trailing-position property must be verified too and this count "
            f"raised deliberately. If the count is ZERO the scan is broken and "
            f"the check below would pass over nothing."
        )

    def test_nothing_is_concatenated_after_value_token(self):
        """No required element may follow the token in a composed pattern.

        This is the whole safety argument. `re.sub(flag_sep + _VALUE_TOKEN, ...)`
        can always succeed once one unit matches; `re.sub(flag_sep +
        _VALUE_TOKEN + anything_required, ...)` can fail, and a failing match is
        what forces the engine through every partition.
        """
        _, sites = self._value_token_use_sites()
        assert sites, "PARSE FAILURE: no `_VALUE_TOKEN` use site to check."

        for line in sites:
            assert re.search(r"_VALUE_TOKEN\s*[,)]", line), (
                f"`_VALUE_TOKEN` IS NO LONGER IN TRAILING POSITION:\n\n"
                f"    {line}\n\n"
                f"Something now follows it in the composed pattern. That is the "
                f"one edit that makes its exponential REACHABLE: with a required "
                f"element after it, a failing match explores every partition of "
                f"the `$`-initial arms — measured at roughly 16x per added unit, "
                f"reaching 28 SECONDS on an 85-character input, and past the "
                f"600 s hook ceiling shortly after. A hook that exceeds that "
                f"ceiling is KILLED AND THE TOOL CALL PROCEEDS, so this is a "
                f"silent guard BYPASS, not a slow refusal.\n\n"
                f"If the addition is genuinely required, the token must first be "
                f"made unambiguous — give each `$`-initial arm a distinguishing "
                f"first character so the partition is unique, the way the "
                f"flag-token walk was repaired."
            )

    def test_value_token_strip_is_fast_on_the_adversarial_witness(self):
        """Bounded absolute check on the shipped consumer at the size where a
        reachable exponential would already be seconds.

        24 units is the point at which the isolated pattern measures 1.75 s
        under a forced failure; the shipped strip does the same work in
        microseconds. The ceiling separates those two, not two nearby numbers.

        COUNTER-TESTED: appending a required element after `_VALUE_TOKEN` in
        `_strip_flag_values` makes this arm measure 2.58 s — a red from one
        added token, on a 75-character input.
        """
        witness = "-m " + "$''" * 24
        elapsed = _best_time(lambda s: _strip_flag_values(s, r"(-m\s+)", lambda m: m.group(1) + "'X'"), witness)
        assert elapsed < 1.0, (
            f"_strip_flag_values took {elapsed * 1000:.1f} ms on a "
            f"{len(witness)}-character witness. Shipped cost here is measured in "
            f"MICROSECONDS, and the isolated pattern costs ~1.75 s at this size "
            f"only when a match can FAIL — so this is the reachable-exponential "
            f"signature. Check whether anything now follows `_VALUE_TOKEN`."
        )

    def test_public_entry_point_is_fast_on_the_adversarial_witness(self):
        """The same bound through `is_dangerous_command`, which is what the hook
        actually calls.

        Not redundant with the check above: that one drives an internal helper
        directly, and a helper being fast does not establish that the path
        REACHING it is. The hook's cost is what the 600 s ceiling applies to, so
        the public entry point is where the claim has to hold.

        THE WITNESS IS `git commit -m`, AND THE CHOICE IS LOAD-BEARING. A
        `gh pr merge --body` witness was tried first and is VACUOUS: it never
        reaches `_strip_flag_values` at all, so it stayed green under the
        mutation below while appearing to cover it. An end-to-end arm whose
        input does not route through the code it names is not a weak test, it is
        a test that cannot fail. Verified by instrumenting the strip and
        recording which command shapes actually call it; `git commit -m`,
        `git merge -m`, `git tag -m`, `gh release create --notes` and
        `gh api -f` do, and the `gh pr merge` surface does not.

        COUNTER-TESTED, with the tree state verified in the same step as the
        measurement: shipped 0.12 ms, and ~10 s once a required element follows
        `_VALUE_TOKEN`. Absolute-only — see the note above `_measure_scaling`
        for why a ratio arm on this witness was built, measured, and rejected.
        """
        witness = "git commit -m " + "$''" * 26
        elapsed = _best_time(is_dangerous_command, witness, k=2)
        assert elapsed < 2.0, (
            f"is_dangerous_command took {elapsed * 1000:.1f} ms on a "
            f"{len(witness)}-character witness; shipped cost is ~0.12 ms. A "
            f"reachable `_VALUE_TOKEN` exponential shows up here, because this "
            f"is the path the hook runs — check whether anything now follows "
            f"the token in `_strip_flag_values`."
        )


class TestGhFlagTokenAmbiguityStaysRemoved:
    """The flag-token walk must stay unambiguous, so its exponential cannot return.

    The repaired constant is `(?:-\\S*(?:\\s+[^-\\s]\\S*)?\\s+)*`. The `[^-\\s]`
    on the optional value arm is the entire fix: a dash-initial token can only be
    a flag and a non-dash-initial token can only be a value, so the partition is
    unique and there is nothing to backtrack over.

    MEASURED HERE, both forms, same machine, through the shipped pattern with
    only that character class reverted — so the ceiling separates a real
    difference rather than a remembered one. Counter-tested by mutation, not
    assumed: reverting the value arm to `(?:\\s+\\S+)?` reds the check below at
    the measured cost, and restoring it returns to microseconds.

        flags   pre-fix (reverted)   shipped
        34               1.554 s     0.0093 ms
        36               4.170 s     ~0.01  ms
        38              10.909 s     ~0.01  ms

    36 IS CHOSEN FOR ITS MARGIN IN BOTH DIRECTIONS, and neither direction is
    free. The 1 s ceiling sits 4.2x BELOW the pre-fix cost, so a runner several
    times faster than this one still reds — 34 was rejected for exactly this
    reason, at only 1.6x it could be outrun by a fast machine and miss the
    regression. It sits ~100,000x ABOVE the shipped cost, so no contention can
    red it falsely. And 36 rather than 40 keeps a red TERMINATING in seconds
    rather than the 30 s that 40 costs: a test that hangs reports as
    infrastructure trouble instead of as a defect.

    A comment recording that this was measured once by hand is not a guard —
    the constant it replaced carried exactly such a comment, asserting linearity
    that had never been measured, and that comment is why the exponential
    shipped. This is the same measurement wired to fail.
    """

    def test_consecutive_valueless_flags_do_not_backtrack(self):
        witness = "gh " + "-a " * 36
        elapsed = _best_time(lambda s: _GH_PR_NUMBER_RE.search(s), witness)
        assert elapsed < 1.0, (
            f"_GH_PR_NUMBER_RE took {elapsed * 1000:.1f} ms on {witness[:24]!r}"
            f"... (36 valueless dash-flags). Shipped cost is ~0.01 ms; the "
            f"ambiguous pre-fix form costs ~4.2 s here. The optional value arm "
            f"has almost certainly lost its `[^-\\s]` first-character "
            f"constraint, so a token can again be either a flag or the previous "
            f"flag's value and every added token doubles the partitions."
        )

    # THE REPAIR ALSO CHANGED WHICH DIGIT IS CAPTURED, so it is not only a
    # backtracking fix. Where the flag run stops decides which token is read as
    # the positional pull-request number, and the unconstrained predecessor
    # stops elsewhere — on a good-faith `gh pr merge --squash --subject 2024 42`
    # it captures 2024, the flag's own argument, instead of 42. That is a
    # target-identification difference on a guard whose job is to bind an
    # approval to a specific pull request.
    #
    # So this timing check does NOT cover the whole revert. The capture
    # behaviour is pinned in test_merge_guard_pre.py, beside the rest of the
    # extraction corpus, and those pins redden under the same revert this one
    # does — for a different and more serious reason.


class TestShellTokenizerCostIsBoundedByCommandLength:
    """`shlex.split` is super-linear on quote-dense input. ACCEPTED, not fixed.

    THIS IS NOT A DENIAL-OF-SERVICE FINDING and must not be reported as one.
    The cost is bounded by command length, and command length is bounded by what
    a person types.

    MEASURED through the shipped `_shell_tokenize`, and the pairing is the
    finding: the cost tracks QUOTE DENSITY, and is indifferent to whether the
    quotes balance. An odd quote count raises inside shlex and the wrapper
    abstains; an even one tokenises successfully. Both cost the same:

        length    odd quotes (abstains)   even quotes (succeeds)
         40,001              7.19 ms                  7.08 ms
        160,001             75.59 ms                 74.95 ms
        320,001            291.76 ms                288.56 ms

    k is roughly 1.7-2.2 on both. THE CONTROL IS WHAT LOCATES THE CAUSE: plain
    input with NO quotes measures 1.99-2.03x per doubling — clean linear — so
    the super-linearity belongs to quote handling, not to tokenisation in
    general and not to the failure path.

    That last point is worth stating plainly because the natural reading is the
    opposite one. This cost is NOT confined to malformed commands; a
    well-formed, successfully-tokenised command pays exactly the same. Reaching
    it needs no mistake at all — only quotes.

    AT REAL COMMAND LENGTHS THE COST IS STILL NEGLIGIBLE, which is what makes
    the acceptance sound: 0.015 ms at 100 characters, 0.27 ms at 2,000, 1.14 ms
    at 8,000.

    THE RISK IS RATIFIED, AND DELIBERATELY NOT GUARDED BY A TIMING TEST.
    Accepted by the team lead on review of these measurements, on the grounds
    the issue itself states: the finding is LOW and must not be reported as a
    denial-of-service. Recorded here rather than only in the tracker so the
    acceptance is discoverable from the code it applies to.

    NO RECURRING TIMING ARM IS SHIPPED FOR THIS, and the omission is the
    decision rather than an oversight. A wall-clock arm on an ACCEPTED risk
    buys no gate value — there is no defect for it to catch, because the
    super-linearity is expected and permitted — while adding flake surface to
    a suite where a timing bound reding on a loaded runner is a known,
    separately-tracked problem. The standing rule for a finding with no
    defensible action is to RECORD it, not to answer it with a test. The
    numbers above are the record; only the behavioural contract below is
    pinned, and it costs no time to assert.
    """

    def test_unbalanced_input_abstains_rather_than_tokenising(self):
        """The failure mode is abstention, which is why the cost is tolerable —
        callers fall back to the literal floor rather than trusting a partial
        tokenisation.

        The witness carries an ODD quote count deliberately. `"a'" * n` looks
        unbalanced and is not: the quotes pair off and shlex returns a single
        token, so an even-count witness silently exercises the success path
        while reading as the failure path.
        """
        assert _shell_tokenize("a'" * 100 + "'") is None
        assert _shell_tokenize("a'" * 100) == ["a" * 100]
        assert _shell_tokenize("git push origin main") == ["git", "push", "origin", "main"]
