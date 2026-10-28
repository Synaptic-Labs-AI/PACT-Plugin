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

* **Absolute CPU-time ceiling** (PRIMARY discriminator). The unbounded/quadratic
  form is ~1.8 s (gh detect) to ~7.3 s (gh read) at N=4000. The ceiling sits
  clear of linear and below quadratic, so it cannot flap on a slow machine yet
  still trips on a regression.

  TIMED ON CPU TIME (``time.process_time``), NOT WALL CLOCK. The work is pure
  CPU, so the two clocks agree on a quiet machine, and backtracking burns CPU
  either way. Wall clock also counts the time the process waits for a core,
  so concurrent suites failed these ceilings on honest code; CPU time does not
  count that wait.

  BOUNDED COSTS ARE QUOTED AT ``N_LARGE`` BELOW, NOT AT N=4000. Every figure
  in this module once described N=4000 while the arms ran at 8000, which
  understated one of them ~30x and makes a ceiling look safer than it is. Each
  per-case comment now carries its measured ``t_large`` and the margin that
  follows from it.
* **Scaling ratio** ``t(2N)/t(N) < 3.0`` across one doubling. Linear ≈ 2.0,
  quadratic ≈ 4.0; 3.0 is the midpoint. ``best-of-K`` minimum timing suppresses
  upward scheduler/GC noise (a slow sample never lowers the min).

Counter-test-by-revert (non-vacuity)
------------------------------------
Restore the unbounded ``*`` form (the shared prefix constants) and re-run: the
ratio returns to ~4x AND the N=4000 CPU time blows past the ceiling => the
targeted case goes RED. Expected cardinality: reverting the shared constants
(``_GIT_GLOBAL_FLAGS`` / ``_GH_GLOBAL_FLAGS``) reds the corresponding git / gh
cases.
"""

import time
from pathlib import Path

import pytest

from merge_guard_pre import is_dangerous_command  # noqa: E402
from shared.merge_guard_common import detect_command_operation_type  # noqa: E402

# N_LARGE = 2 * N_SMALL — the scaling-ratio doubling. N is large enough that the
# unbounded quadratic is unmistakable (~6 s for the git bank) yet the bounded
# form stays well under ~0.2 s, so the test runs in a few seconds.
#
# FLOORED (measured): a RATIO removes machine-SPEED dependence but NOT NOISE
# dependence, and a small denominator amplifies what is left. At N_SMALL=2000 the
# smallest arm measured ~33 ms, so a single scheduler slice is a large fraction
# of it; under a loaded suite this ratio was observed at 3.85 against a 3.0
# ceiling while the honest value is ~2.0. That is a false red, and it is also
# uncomfortably close to the 4.0 quadratic signature the ceiling exists to
# separate — so the noise threatened BOTH directions, not just flakiness.
# Doubling N puts every arm in the hundreds of milliseconds, where a scheduler
# slice is a few percent rather than tens of percent.
N_SMALL = 4000
N_LARGE = 8000

# best-of-K minimum: the dominant timing noise is upward (scheduler preemption,
# GC), and a minimum is a clean lower bound on the true cost that a slow sample
# cannot inflate. K=5 gives five chances at a clean large-N measurement, which
# is what keeps the small-absolute-time ratios (gh detect, push-flag walk) from
# flaking.
_K = 5

# Linear ~2.0x per doubling; quadratic ~4.0x. 3.0 is the midpoint.
RATIO_CEILING = 3.0

# Per-case absolute ceilings. The git/detect bank's quadratic is ~6 s at N=4000
# and 60.7 s at N_LARGE (measured under the counter-factual revert), so 2.0 s
# trips hard on regression.
# MEASURED t_large at N_LARGE=8000, across quiet / 10 / 24 busy procs / two
# concurrent full-suite runs: is_dangerous 286-435 ms (4.6x of room),
# detect 375-619 ms (3.2x). Both hold.
_CEIL_GIT = 2.0


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


# gh multi-anchor witness (review m-3 — the `_GH_GLOBAL_FLAGS` bound had no direct
# perf witness). Measured quadratic counter-factual (revert `_GH_GLOBAL_FLAGS`
# `{0,K}`->`*`): is_dangerous ~7.3 s at N=4000 (2.0 s ceiling fine), but
# detect_command_operation_type only ~1.8 s (two gh-prefix classifier patterns,
# not the ~21-pattern read bank) — so detect/gh gets a TIGHTER 1.0 s ceiling
# (per-surface ceiling calibration).
# MEASURED t_large at N_LARGE=8000, same four load levels: is_dangerous
# 214-363 ms against 2.0 s (5.5x of room), detect 221-373 ms against 1.0 s
# (2.7x — the tightest margin in this module, and it holds). The `~0.08 s /
# ~0.02 s` this replaced was an N=4000 figure; the detect half understated
# N_LARGE by ~15x.
_CEIL_GH_READ = 2.0
_CEIL_GH_DETECT = 1.0

# Push-flag-walk witness (`git push ` + `-x ` * N). The push-dash-flag walk is a
# SINGLE-anchor walk (one `git`/`push`), so even unbounded it is O(N) LINEAR, not
# quadratic — it was already linear at HEAD (the F1 outer `_GIT_PREFIX` bound caps
# the multi-anchor interaction; design §12.2). This case PINS structural linearity
# (ratio < 3.0): a future nested-quantifier regression in the push patterns would
# trip it. It is GREEN-stays-GREEN — reverting the `{0,32}` push bound keeps it
# linear, so there is NO perf counter-test-RED for it; the bound's non-vacuity is
# the >K-RESIDUAL FLIP in TestFlagTokenBoundary, not a perf revert.
#
# THE RATIO ASSERTION IS DISABLED FOR THIS CASE (ratio ceiling None below), and
# the reason is that it is the ONE arm here that can produce a false red and
# cannot produce a true one. VERIFIED, not inferred: reverting both global-flag
# bounds `{0,32}`->`*` reds `detect_command_operation_type/git` at 60.7 s against
# its 2.0 s ceiling and leaves BOTH push cases green — so the counter-factual
# that exists for git/gh does not reach these, exactly as the paragraph above
# says. What remains is a guard against a hypothetical future nested-quantifier
# regression, and the absolute ceiling below guards the same hypothetical with
# far more room:
#
#   MEASURED at N_LARGE=8000, 38 reps per case across four load levels (quiet;
#   10 and 24 busy procs on 10 cores; two concurrent full-suite pytest runs;
#   and a 2M-object live heap): ratio med 1.99, MAX 2.79 against a 3.0 ceiling
#   — 1.07x of room. t_large med 175-310 ms against the 1.0 s ceiling below —
#   3.3x of room. Neither is counter-testable today; keeping the one that will
#   not false-fire and dropping the one that will is the whole trade.
#
# The `~6 ms` this comment used to claim was stale by the N doubling above and
# understated the real figure by ~30x, which makes the absolute ceiling look far
# safer than it is. Measured numbers, with their N, replace it.
_CEIL_PUSHWALK = 1.0

# `_VALUE_TOKEN` HAS NO ARM IN THIS LADDER, AND THE ATTEMPT IS RECORDED SO IT IS
# NOT REPEATED. A ratio arm on the `$''` witness was built, sized into the
# hundreds-of-milliseconds band this N floor exists to reach (254 ms / 783 ms),
# and it STILL FLAKED: measured 1.98 in isolation and 3.08 under a loaded
# full-suite run, against the 3.0 ceiling. Being in the band is NECESSARY BUT
# NOT SUFFICIENT — the floor fixes a small-denominator problem, and this is a
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


def _measure_scaling(fn, build):
    """Return (t_small, t_large) best-of-K times on the witness ``build(N)``."""
    # Warm up once (regex objects are compiled at import; this primes caches).
    fn(build(100))
    t_small = _best_time(fn, build(N_SMALL))
    t_large = _best_time(fn, build(N_LARGE))
    return t_small, t_large


# (id, function, witness builder build(N)->str, absolute-ceiling-seconds,
#  ratio-ceiling or None to skip the ratio assertion — None ONLY where the case
#  has no quadratic counter-factual, so the ratio can false-red but never true-red)
_CASES = [
    ("is_dangerous_command/git", is_dangerous_command, lambda n: "git x " * n, _CEIL_GIT, RATIO_CEILING),
    ("detect_command_operation_type/git", detect_command_operation_type, lambda n: "git x " * n, _CEIL_GIT, RATIO_CEILING),
    # --- remediation additions (PR #1003) ---
    ("is_dangerous_command/gh", is_dangerous_command, lambda n: "gh x " * n, _CEIL_GH_READ, RATIO_CEILING),
    ("detect_command_operation_type/gh", detect_command_operation_type, lambda n: "gh x " * n, _CEIL_GH_DETECT, RATIO_CEILING),
    ("is_dangerous_command/push-flag-walk", is_dangerous_command, lambda n: "git push " + "-x " * n, _CEIL_PUSHWALK, None),
    ("detect_command_operation_type/push-flag-walk", detect_command_operation_type, lambda n: "git push " + "-x " * n, _CEIL_PUSHWALK, None),
]


@pytest.mark.parametrize(
    "fn,build,abs_ceiling,ratio_ceiling",
    [(fn, build, ceil, rceil) for (_id, fn, build, ceil, rceil) in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_global_flag_prefix_scaling_is_subquadratic(fn, build, abs_ceiling, ratio_ceiling):
    """The bounded global-flag prefixes / push-flag walk must scale
    sub-quadratically on the worst-case witness through both detection paths,
    the gh prefix (review m-3), and the push-flag walk
    (structural-linearity pin, §12.2). Restoring an unbounded `*` form on a
    MULTI-anchor witness (git/gh) makes the ratio ~4x and the N=4000
    CPU time exceed the ceiling => RED. The push-flag-walk witness is
    single-anchor (already linear, defense-in-depth) so it stays GREEN under
    revert — its non-vacuity lives in TestFlagTokenBoundary's >K-residual flip."""
    t_small, t_large = _measure_scaling(fn, build)
    ratio = (t_large / t_small) if t_small > 0 else float("inf")
    witness = build(1)

    # PRIMARY: generous absolute CPU-time ceiling (flake-resistant).
    assert t_large < abs_ceiling, (
        f"{fn.__name__} on {witness!r}*{N_LARGE}: {t_large * 1000:.1f} ms exceeds "
        f"{abs_ceiling * 1000:.0f} ms ceiling — unbounded O(n^2) backtracking regression?"
    )

    # REINFORCING: scaling ratio across one doubling (linear ~2.0, quadratic ~4.0).
    # Skipped where ratio_ceiling is None — see the `_CEIL_PUSHWALK` note.
    if ratio_ceiling is not None:
        assert ratio < ratio_ceiling, (
            f"{fn.__name__} on {witness!r}: t({N_LARGE})/t({N_SMALL}) = {ratio:.2f} "
            f">= {ratio_ceiling} — quadratic scaling regression "
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
