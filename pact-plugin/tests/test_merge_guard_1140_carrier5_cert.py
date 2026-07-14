"""
Location: pact-plugin/tests/test_merge_guard_1140_carrier5_cert.py
Summary: BIDIRECTIONAL certification for #1140 — carrier-5 (`git commit -m/--message`
         message-value strip) migrated from two `-m`-only inline `re.sub` arms to the
         shared span-bounded quote-balanced machinery (`_strip_flag_values` /
         `_VALUE_TOKEN` / `_keep_carrier_value`), a verb-only clone of carrier-7d
         (`git tag -m`). Certifies the fix against the REAL `is_dangerous_command`,
         base-vs-HEAD, NEVER a byte-diff / additive-lines argument (the #1118 trap:
         a +N/-0 change to the shared strip/leg-split pipeline opened BOTH an over-block
         and an under-block, invisible to a byte-diff — only behavioral data-flow review
         against the real classifier catches it).

         THREAT POLARITY (SACROSANCT merge_guard control):
           - OVER-block = cardinal sin: blocking a faithful `git commit` click is wrong
             by definition. The five residuals below were each a real over-block at base.
           - UNDER-block = security hole the fix MUST NOT open. Leg-locality is the
             load-bearing invariant: the value-strip span stops at the first UNQUOTED
             ;/&&/|/newline, so an executing destructive tail stays OUTSIDE the stripped
             span and is caught.

         WHAT IS CERTIFIED (design §5 matrix, 26 vectors):
           - OVER-BLOCK CLOSURE (5): multi `-m`, ANSI-C $'...', adjacent-concat,
             `--message` single, `git -C <path> commit` — each is_dangerous on BASE
             (over-blocked) and NOT on HEAD (faithful click freed).
           - FAITHFUL NEVER-BLOCKED (5): benign / prose-bearing single `-m`/`--message`/
             `-C commit` forms stay False (incl. the issue #1140 body's exact repro,
             a single-`-m` dq form that was already handled by the old arm-1).
           - UNDER-BLOCK RETENTION (8): &&/; tails (spaced + unspaced), piped body,
             $()-body, >(bash) procsub, and the u8 adversarial later-`git commit` canary
             — each stays gated (True) on both base and HEAD.
           - WIDENED-PREFIX UNDER-BLOCK (3): the global-flag prefix (OQ1) surface —
             `-C "$()"`, `-C … && tail`, `-C … ; tail` — stays gated.
           - EXISTING-TEST-PIN COMPATIBILITY (4): the fix leaves 4 pre-existing
             behavioral pins unchanged (design §5.6).

NON-VACUITY (base-vs-HEAD, in-test + permanent): the OVER-BLOCK and UNDER-BLOCK rows load
the PRE-FIX parent classifier (f6e3639a = 2d7fcd07^, the `-m`-only inline arms) via
`git show` + exec and assert the discrimination IN-TEST (base=True), so a future strip
regression that re-breaks carrier-5 flips a HEAD column back and reds the row, and the
base column proves each form was a genuine vector (never a vacuous green). This baked-
classifier discriminator is crash-atomic (NO working-tree mutation — the fix is COMMITTED,
so a `git checkout HEAD` would restore the FIXED baseline; this avoids that trap entirely)
and mirrors test_merge_guard_1129_r2_cert.py.

THE GOBBLING NEGATIVE CONTROL (the load-bearing non-gobbling-word-class proof, design §5.5):
`TestU8AdversarialAndGobblingControl` builds a MUTANT of the live source with carrier-5's
non-gobbling `[^;&|\\n\\s]` word class swapped for a `\\S` gobbler and proves the u8
adversarial canary flips True (caught) -> False (UNDER-BLOCK OPENED) under the mutant. This
is the empirical proof (not a static argument) that the `[^;&|\\n\\s]` word class is
load-bearing — a `\\S+` gobbler crosses two `;` separators to re-anchor on a LATER
`git commit`, pulls the intermediate `;gh pr merge 5;` into ONE span, and `_strip_flag_values`
arm-3's `_VALUE_TOKEN` (which does NOT stop at ;&|) then eats `STRIPPED;gh` -> the `gh` head
is deleted -> the merge is unrecognized -> auto-ALLOW.

Cross-refs: docs/architecture/1140-carrier5.md §5 (cert matrix) + §5.5 (gobbling control);
docs/preparation/1140-carrier5.md Appendix A (global-flag-prefix under-block mapping).
Destructive verbs are assembled at runtime (BD/BDR/PF/M5) so this file carries no raw
force-delete / force-push / merge literal and stays inert to the live guard.
"""
import subprocess
import sys
import types
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.merge_guard_common as mgc  # noqa: E402

D = mgc.is_dangerous_command
STRIP = mgc._strip_non_executable_content

# --- Baked PRE-FIX classifier loaded from git ONCE for in-test base-vs-HEAD non-vacuity.
#     BASE = the fix commit's PARENT (the `-m`-only inline arms carrier-5). The over-block
#     residuals are is_dangerous=True on BASE and False on HEAD; the under-block canaries
#     are True on both. Loading the parent as a module (NOT a working-tree checkout) keeps
#     this crash-atomic and sidesteps the "checkout-HEAD restores the fixed baseline" trap.
_BASE_SHA = "f6e3639a"  # 2d7fcd07^ — pre-carrier-5-fix (v4.6.4)


def _load_classifier(sha):
    """Load merge_guard_common as it existed at `sha`, or None if unavailable.

    Returns None on any git/exec failure — git missing, or a SHALLOW clone lacking the
    parent commit (CI default fetch-depth) — so collection SUCCEEDS and the base-vs-HEAD
    differential rows self-SKIP (@requires_history) instead of aborting the file. Mirrors
    test_merge_guard_1129_r2_cert._load_classifier.
    """
    wt = Path(__file__).resolve().parents[2]  # worktree root (tests/../../)
    try:
        src = subprocess.check_output(
            ["git", "-C", str(wt), "show",
             sha + ":pact-plugin/hooks/shared/merge_guard_common.py"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    mod = types.ModuleType("merge_guard_common_1140_" + sha)
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"  # so its `from shared.x import ...` resolve on sys.path
    try:
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    except Exception:
        return None
    return mod


_BASE = _load_classifier(_BASE_SHA)
# None-safe: a bare `_BASE.is_dangerous_command` would AttributeError at import when the
# parent source is unavailable (shallow clone), re-aborting collection. D_BASE is only ever
# called by the @requires_history-guarded differential rows.
D_BASE = _BASE.is_dangerous_command if _BASE is not None else None

requires_history = pytest.mark.skipif(
    _BASE is None,
    reason="base-vs-HEAD differential requires the fix parent in history (shallow clone / missing history)",
)


# --- Destructive verbs assembled at runtime — this file carries no raw literal.
BD = "git " + "branch " + "-D victim"          # destructive branch-delete (prose target)
BDR = "git " + "branch " + "-D real"           # destructive branch-delete (executing tail)
PF = "git " + "push " + "--force origin main"  # destructive force-push
M5 = "gh " + "pr " + "merge 5 --delete-branch"  # destructive merge


def _load_gobbling_mutant():
    """Load the LIVE source with carrier-5's non-gobbling `[^;&|\\n\\s]` word class swapped
    for a `\\S` gobbler — the design's REJECTED variant (§5.5). Used to prove the word class
    is load-bearing: the mutant re-opens the u8 leg-merge under-block. The needle includes
    the `commit\\b` anchor so ONLY carrier-5's span is mutated (carrier-7d's identical `tag`
    span is left intact). Asserts the needle exists so a future source drift fails LOUDLY
    here rather than silently no-op'ing the negative control into a vacuous pass."""
    src = Path(mgc.__file__).read_text()
    needle = r'r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}commit\b"'
    repl = r'r"\bgit\s+(?:\S+\s+){0,%d}commit\b"'
    assert needle in src, "carrier-5 non-gobbling span literal not found — source drifted; " \
                          "the gobbling negative control would be vacuous"
    mutant_src = src.replace(needle, repl)
    assert mutant_src != src
    mod = types.ModuleType("merge_guard_common_1140_gobble")
    mod.__file__ = mgc.__file__
    mod.__package__ = "shared"
    exec(compile(mutant_src, mod.__file__, "exec"), mod.__dict__)
    return mod


def _carrier5_span_source():
    """The carrier-5 CODE block — the `_git_commit_span = (...)` assignment + its `re.sub`
    call, scoped from the assignment to the `# 6.` marker. Deliberately EXCLUDES the
    preceding explanatory comment, which NAMES the rejected `_GIT_PREFIX` / `(?:\\S+\\s+)`
    gobbler to document why it is not used — the drift-detector below asserts on executable
    code, not prose, so the comment's mention of the rejected form does not trip it."""
    src = Path(mgc.__file__).read_text()
    start = src.index("_git_commit_span = (")
    end = src.index("# 6. ", start)
    return src[start:end]


# ===========================================================================
# OVER-BLOCK CLOSURE — faithful click freed (base is_dangerous True -> HEAD False).
# The message merely MENTIONS a destructive op; the command itself is a single faithful
# commit. base=True (the bug) proves non-vacuity; HEAD=False is the fix.
# ===========================================================================
class TestOverBlockClosure:

    @pytest.mark.parametrize("label,cmd", [
        ("a multi -m",       'git commit -m "subj" -m "body: run %s to clean up"' % BD),
        ("b ANSI-C $'...'",  "git commit -m $'fix: describe %s in prose'" % BD),
        ("c adjacent-concat", 'git commit -m "prefix ""%s"' % BD),
        ("d --message",      'git commit --message "fix: describe %s in prose"' % BD),
        ("e git -C commit",  'git -C /some/repo commit -m "fix: describe %s in prose"' % BD),
    ])
    @requires_history
    def test_over_block_closed_base_true_head_false(self, label, cmd):
        assert D_BASE(cmd) is True, \
            "%s: expected a BASE over-block vector (else the closure row is vacuous): %r" % (label, cmd)
        assert D(cmd) is False, \
            "%s: HEAD must CLOSE the faithful-click over-block (cardinal sin if it stays blocked): %r" % (label, cmd)


# ===========================================================================
# FAITHFUL NEVER-BLOCKED — benign / prose-bearing single forms stay False at HEAD.
# The PRIMARY certification gate: no faithful click is ever blocked.
# ===========================================================================
class TestFaithfulNeverBlocked:

    @pytest.mark.parametrize("label,cmd", [
        ("single dq benign",      'git commit -m "some message"'),
        ("issue #1140 repro",     'git commit -m "fix: describe %s in prose"' % BD),
        ("single sq prose",       "git commit -m 'fix: describe %s in prose'" % BD),
        ("-C benign",             'git -C /repo commit -m "benign message"'),
        ("--message benign",      'git commit --message "benign"'),
    ])
    def test_faithful_stays_false_at_head(self, label, cmd):
        assert D(cmd) is False, "%s: a faithful commit click must NEVER be blocked: %r" % (label, cmd)

    def test_issue_repro_was_never_the_bug(self):
        # The issue #1140 body's exact repro is a single-`-m` dq form the OLD arm-1 already
        # stripped, so it was False on base too (the bug was the OTHER forms — multi-m,
        # ANSI-C, adjacent-concat, --message, -C). Documenting base=False keeps the faithful
        # controls honest: they are not silently over-block-closure rows in disguise.
        cmd = 'git commit -m "fix: describe %s in prose"' % BD
        if D_BASE is not None:
            assert D_BASE(cmd) is False


# ===========================================================================
# UNDER-BLOCK RETENTION — executing destructive tail/body stays gated (base & HEAD True).
# base=True proves the danger is real (non-vacuous); HEAD=True proves the fix opened no hole.
# ===========================================================================
class TestUnderBlockRetained:

    @pytest.mark.parametrize("label,cmd", [
        ("u1 && tail",        'git commit -m "msg" && %s' % BDR),
        ("u2 &&-nospace",     'git commit -m "msg"&&%s' % BDR),
        ("u3 ; tail",         'git commit -m "x" ; %s' % M5),
        ("u4 ;-nospace",      'git commit -m "x";%s' % M5),
        ("u5 piped body",     'git commit -m "run %s" | bash' % BD),
        ("u6 $()-body",       'git commit -m "$(%s)"' % M5),
        ("u7 >(bash) procsub", 'git commit -m "run %s" > >(bash)' % BD),
        ("u8 adversarial",    'git commit -m "x";%s;git commit -m "y"' % M5),
    ])
    @requires_history
    def test_under_block_stays_gated_base_and_head(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: must be gated on base (else vacuous): %r" % (label, cmd)
        assert D(cmd) is True, "%s: the fix must NOT open an under-block here: %r" % (label, cmd)


# ===========================================================================
# WIDENED-PREFIX UNDER-BLOCK — the OQ1 global-flag-prefix surface stays gated.
# A2: the strip touches ONLY the -m value; a -C value rides in the span PREFIX (never
# stripped) and stays visible. A1/A3: the widened prefix + body still stop at a separator.
# ===========================================================================
class TestWidenedPrefixUnderBlock:

    @pytest.mark.parametrize("label,cmd", [
        ("w1 -C $() value",   'git -C "$(%s)" commit -m "msg"' % M5),
        ("w2 -C ... && tail", 'git -C /repo commit -m "msg" && %s' % BDR),
        ("w3 -C ... ; tail",  'git -C /repo commit -m "x";%s' % M5),
    ])
    @requires_history
    def test_widened_prefix_stays_gated_base_and_head(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: must be gated on base (else vacuous): %r" % (label, cmd)
        assert D(cmd) is True, "%s: the global-flag prefix must NOT open an under-block: %r" % (label, cmd)


# ===========================================================================
# THE TWO HIGHEST-VALUE PINS (architect-flagged): u8 adversarial + gobbling negative control.
# ===========================================================================
class TestU8AdversarialAndGobblingControl:

    _U8 = 'git commit -m "x";%s;git commit -m "y"' % M5

    def test_u8_stays_caught_at_head(self):
        # A later `git commit` must NOT let the non-gobbling prefix cross the two `;` to
        # re-anchor and swallow the middle `gh pr merge 5 --delete-branch` — it stays visible.
        assert D(self._U8) is True, "u8: the middle merge must stay caught at HEAD: %r" % self._U8
        # Mechanism witness: the middle merge survives on the stripped surface.
        assert M5 in STRIP(self._U8), \
            "u8: the middle merge must remain on the stripped surface: %r" % STRIP(self._U8)

    def test_gobbling_mutant_opens_the_underblock(self):
        # LOAD-BEARING NEGATIVE CONTROL (design §5.5): swap carrier-5's non-gobbling
        # `[^;&|\n\s]` word class for a `\S` gobbler and prove u8 flips True -> False.
        # The gobbler crosses both `;` to reach the later `git commit`, pulls `;gh pr merge 5;`
        # into ONE span, and arm-3's `_VALUE_TOKEN` eats `STRIPPED;gh` — deleting the `gh`
        # head so the merge is unrecognized -> auto-ALLOW. This is the EMPIRICAL proof (not a
        # static argument) that the non-gobbling word class is load-bearing.
        mutant = _load_gobbling_mutant()
        assert D(self._U8) is True, "sanity: real classifier catches u8"
        assert mutant.is_dangerous_command(self._U8) is False, \
            "the gobbling mutant MUST re-open the u8 under-block (else the negative control is vacuous)"
        # The gh head is eaten on the mutant's stripped surface (gone), present on the real one.
        assert M5 not in mutant._strip_non_executable_content(self._U8), \
            "the gobbling mutant must EAT the gh head (the under-block mechanism)"

    def test_source_uses_nongobbling_word_class(self):
        # SOURCE-LEVEL DRIFT-DETECTOR — DESIGN-INTENT, deliberately anchored to the exact
        # word class, NOT brittle: this pins that carrier-5's global-flag prefix uses the
        # non-gobbling `[^;&|\n\s]` word class and NOT a `\S+` gobbler and NOT `_GIT_PREFIX`
        # (whose `(?:\S+\s+){0,N}` gobbler spans separators). Per the behavioral proof above,
        # a gobbling prefix re-opens a leg-merge UNDER-BLOCK (auth bypass). A future reader
        # tempted to "de-brittle" this by loosening the word class to `\S+` or reusing
        # `_GIT_PREFIX` would silently ship that security hole — this assertion is the
        # tripwire that stops it. Do NOT relax it.
        block = _carrier5_span_source()
        assert r"[^;&|\n\s]" in block, \
            "carrier-5 prefix must use the non-gobbling [^;&|\\n\\s] word class"
        assert r"(?:\S+\s+){0," not in block, \
            "carrier-5 prefix must NOT use a \\S+ gobbler (re-opens the leg-merge under-block)"
        assert "_GIT_PREFIX" not in block, \
            "carrier-5 prefix must NOT reuse _GIT_PREFIX (its \\S+ gobbler spans separators)"


# ===========================================================================
# EXISTING-TEST-PIN COMPATIBILITY (design §5.6) — the fix leaves these unchanged.
# ===========================================================================
class TestExistingPinCompatibility:

    @pytest.mark.parametrize("label,cmd", [
        # A quoted `&& b` inside the message is a VALUE, not a leg boundary; the real `&&`
        # tail is an executing destructive leg -> stays gated on base and HEAD.
        ("quoted-&& value + close tail", 'git commit -m "a && b" && gh pr close 42 --delete-branch'),
        ("quoted-&& value + branch-delete tail", 'git commit -m "a && b" && %s' % ("git " + "branch " + "-D temp")),
        # commit + merge + tag in one command (the #1129 R2 cross-carrier pin).
        ("commit;merge;tag",            'git commit -m "x";gh pr merge 5;git tag v1'),
    ])
    @requires_history
    def test_pin_stays_gated_base_and_head(self, label, cmd):
        assert D_BASE(cmd) is True and D(cmd) is True, \
            "%s: existing behavioral pin must be unchanged by the fix: %r" % (label, cmd)

    def test_strip_surface_pin_gh_pr_merge_removed(self):
        # test_merge_guard.py:2064 pins that a single-`-m` message containing `gh pr merge`
        # prose is stripped to an inert bareword (the value never survives). Byte-identical
        # to HEAD's old single-`-m` output (`git commit -m STRIPPED`).
        result = STRIP('git commit -m "gh pr merge 42"')
        assert "gh pr merge" not in result, "the -m value must be stripped: %r" % result


# ===========================================================================
# STRIP-SURFACE MECHANISM PINS — document HOW each closure/retention happens (the exact
# post-strip surface), so a regression that changes the mechanism (not just the boolean)
# is visible. These ride on the real _strip_non_executable_content.
# ===========================================================================
class TestStripSurfaceMechanism:

    def test_multi_m_both_values_stripped(self):
        # Both -m values become inert -> no destructive prose survives.
        s = STRIP('git commit -m "subj" -m "body: run %s to clean up"' % BD)
        assert s == "git commit -m STRIPPED -m STRIPPED", s

    def test_ansi_c_value_consumed_atomically(self):
        s = STRIP("git commit -m $'fix: describe %s in prose'" % BD)
        assert s == "git commit -m STRIPPED", s

    def test_adjacent_concat_fully_stripped(self):
        s = STRIP('git commit -m "prefix ""%s"' % BD)
        assert s == "git commit -m STRIPPED", s

    def test_dashC_commit_prefix_matched(self):
        s = STRIP('git -C /some/repo commit -m "fix: describe %s in prose"' % BD)
        assert s == "git -C /some/repo commit -m STRIPPED", s

    def test_leg_locality_semicolon_tail_survives(self):
        # u4: the span stops at the first unquoted `;`, so the executing merge tail survives.
        s = STRIP('git commit -m "x";%s' % M5)
        assert s == "git commit -m STRIPPED;%s" % M5, s

    def test_widened_prefix_dashC_cmdsub_value_preserved(self):
        # w1: the $() in the -C value rides in the span PREFIX and is NEVER stripped.
        s = STRIP('git -C "$(%s)" commit -m "msg"' % M5)
        assert s == 'git -C "$(%s)" commit -m STRIPPED' % M5, s
