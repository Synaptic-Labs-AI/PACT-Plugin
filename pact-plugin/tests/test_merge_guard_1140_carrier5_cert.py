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

# --- REMEDIATION baselines (#1176). The first carrier-5 fix (2d7fcd07) shipped a BLOCKING
#     under-block that an independent review caught; two follow-up fixes now sit on top:
#       FIRSTFIX 2d7fcd07 — the FLAWED first fix (naive `'[^']*'` body). Introduced the
#                           escaped-quote/ANSI-C leg-merge UNDER-block for COMMIT.
#       FIXR     b6418727 — bash-faithful shared `_VERB_MSG_BODY` + `_VALUE_TOKEN`. Closed
#                           that under-block (commit AND tag). Did NOT touch the anchor.
#       C4       f7f370a3 — widened the value-strip anchor to `-[a-ln-zA-Z]*m`. Closed the
#                           bundled/attached `-m` OVER-block (commit AND tag). = HEAD.
#     THE TWO-BASELINE SUBTLETY (the non-vacuity crux — the "was-broken" baseline differs
#     per fix AND per carrier; each row asserts the SHARPEST discriminator that ISOLATES the
#     fix responsible, empirically ground-truthed across all four classifiers):
#       - C4 over-block closures (bundled/attached/absurd, commit+tag): FIX-R left the anchor
#         untouched, so these are STILL over-blocked at b6418727. Discriminator that isolates
#         C4 = D_FIXR(cmd) is True -> HEAD False. (A single f6e3639a baseline would conflate
#         C4 with the whole arc and leave the cert blind to an anchor-only regression.)
#       - FIX-R over-block closures (esc-idiom/ANSI-C prose, commit+tag): still over-blocked
#         after the first fix. Discriminator that isolates FIX-R = D_FIRSTFIX is True -> HEAD False.
#       - First-fix-closed over-blocks (locale $"…", adjacent-concat): closed by 2d7fcd07
#         already. Discriminator = D_BASE (f6e3639a) is True -> HEAD False.
#       - FIX-R COMMIT under-block (esc/ANSI + tail + trailing-quoted-leg): INTRODUCED by the
#         first fix. Three-point progression D_BASE=True (f6e3639a caught it) / D_FIRSTFIX=False
#         (2d7fcd07 opened it) / HEAD=True (FIX-R closed it). Discriminator = D_FIRSTFIX is False.
#       - FIX-R TAG under-block (same shape): PRE-EXISTING carrier-7d gap (predates #1140).
#         Discriminator = D_BASE (f6e3639a) is False -> HEAD True.
_FIRSTFIX_SHA = "2d7fcd07"  # the flawed first fix (naive body; introduced the commit under-block)
_FIXR_SHA = "b6418727"      # FIX-R bash-faithful body (closed the under-block; pre-C4-anchor)
_FIRSTFIX = _load_classifier(_FIRSTFIX_SHA)
_FIXR = _load_classifier(_FIXR_SHA)
D_FIRSTFIX = _FIRSTFIX.is_dangerous_command if _FIRSTFIX is not None else None
D_FIXR = _FIXR.is_dangerous_command if _FIXR is not None else None

# All three baked baselines come from the same merged history, so one skipif covers them:
# a shallow clone lacking any of them self-SKIPS every differential row (the HEAD-only rows
# — faithful controls, the gobbling mutant, both drift-detectors, ReDoS — still run).
requires_history = pytest.mark.skipif(
    _BASE is None or _FIRSTFIX is None or _FIXR is None,
    reason="base-vs-firstfix-vs-fixR differentials require merged history (shallow clone / missing history)",
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


# ===========================================================================
# ===  #1176 REMEDIATION — FIX-R (bash-faithful body) + C4 (widened anchor)  ===
# The 26 vectors above certified the FLAWED first fix (2d7fcd07). An independent review found
# a BLOCKING under-block that fix missed: an escaped-quote/ANSI-C message + an executing
# destructive tail + a TRAILING QUOTED LEG — a cross-product the first matrix never combined
# (a naive `'[^']*'` body paired a `'` across the separator and swallowed the executing leg).
# FIX-R (b6418727) made the shared verb-message body bash-faithful and closed it (commit AND
# tag); C4 (f7f370a3) widened the value-strip anchor and closed the bundled/attached `-m`
# OVER-block (commit AND tag). The classes below certify the FINAL committed state (HEAD =
# f7f370a3) bidirectionally, each asserting the SHARPEST per-fix discriminator (see the
# two-baseline note by the baked-classifier loaders). OVER-BLOCK is the USER'S TOP PRIORITY
# (a faithful click blocked is wrong by definition) — its closures come first + most thoroughly.
# ===========================================================================

# Bash apostrophe idioms that desync a naive quote matcher (the FIX-R defect trigger).
# Destructive prose is runtime-assembled (BD) so the committed file stays inert to the guard.
_ESC_MSG = r"'don'\''t'"                       # short escaped-single-quote-idiom message
_ANSI_MSG = r"$'don\'t'"                        # short ANSI-C message with an escaped apostrophe
_ESC_PROSE = r"'don'\''t run %s later'" % BD    # faithful esc-idiom prose mentioning a destructive op
_ANSI_PROSE = r"$'don\'t run %s'" % BD          # faithful ANSI-C prose


# --- C4 OVER-BLOCK CLOSURE (bundled/attached/absurd; commit AND tag) — TOP PRIORITY.
class TestC4OverBlockClosure:
    # Discriminator D_FIXR is True: FIX-R left the anchor untouched, so bundled/attached are
    # STILL over-blocked immediately before C4 -> HEAD False. This ISOLATES C4's contribution
    # (a single f6e3639a baseline would conflate C4 with the whole arc and be blind to an
    # anchor-only regression).
    @pytest.mark.parametrize("label,cmd", [
        ("commit -am",            'git commit -am "run %s later"' % BD),
        ("commit -sam",           'git commit -sam "run %s later"' % BD),
        ("commit -Sam",           'git commit -Sam "run %s later"' % BD),
        ("commit -qam",           'git commit -qam "run %s later"' % BD),
        ("commit attached -m\"\"", 'git commit -m"run %s later"' % BD),
        ("commit attached -m''",   "git commit -m'run %s later'" % BD),
        ("commit ABSURD -a*24 m",  'git commit -%sm "run %s later"' % ("a" * 24, BD)),
        ("tag -am",               'git tag -am "run %s later" v1' % BD),
        ("tag attached -m\"\"",    'git tag -m"run %s later" v1' % BD),
    ])
    @requires_history
    def test_c4_closes_bundled_attached(self, label, cmd):
        assert D_FIXR(cmd) is True, \
            "%s: must STILL be over-blocked at FIX-R (b6418727) — else the row does not isolate C4: %r" % (label, cmd)
        assert D(cmd) is False, \
            "%s: C4 must CLOSE the bundled/attached over-block (cardinal sin if blocked): %r" % (label, cmd)

    @pytest.mark.parametrize("n", [8, 40, 200])
    def test_absurd_cluster_zero_residual(self, n):
        # The user's ABSOLUTE over-block principle forbids a bounded {0,N} residual: even an
        # absurd-but-faithful long cluster must be False. C4's m-excluding class is UNBOUNDED.
        cmd = 'git commit -%sm "run %s later"' % ("a" * n, BD)
        assert D(cmd) is False, "absurd -a*%d m cluster must be False (zero residual): %r" % (n, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("commit -am benign",    'git commit -am "genuinely benign message"'),
        ("commit -m\"\" benign",  'git commit -m"genuinely benign"'),
        ("tag -am benign",       'git tag -am "benign" v1'),
        ("plain -m",             'git commit -m "some message"'),
        ("plain --message",      'git commit --message "benign"'),
    ])
    def test_faithful_message_flag_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: a faithful message-flag click must NEVER be blocked: %r" % (label, cmd)


# --- FIX-R OVER-BLOCK CLOSURE (faithful esc-idiom / ANSI-C prose; commit AND tag).
class TestFixROverBlockClosure:
    # Discriminator D_FIRSTFIX is True: still over-blocked after the first fix's naive body ->
    # HEAD False. Isolates FIX-R's contribution (distinct from the first-fix-closed forms).
    @pytest.mark.parametrize("label,cmd", [
        ("commit esc-idiom prose", "git commit -m %s" % _ESC_PROSE),
        ("commit ANSI-C prose",    "git commit -m %s" % _ANSI_PROSE),
        ("tag esc-idiom prose",    "git tag -m %s v1" % _ESC_PROSE),
        ("tag ANSI-C prose",       "git tag -m %s v1" % _ANSI_PROSE),
    ])
    @requires_history
    def test_fixr_closes_esc_ansi_over_block(self, label, cmd):
        assert D_FIRSTFIX(cmd) is True, \
            "%s: must STILL be over-blocked at the first fix (2d7fcd07) — else does not isolate FIX-R: %r" % (label, cmd)
        assert D(cmd) is False, \
            "%s: FIX-R must CLOSE the faithful esc-idiom/ANSI-C over-block: %r" % (label, cmd)


# --- FIRST-FIX OVER-BLOCK CLOSURE RETAINED (locale $"…", adjacent-concat).
class TestFirstFixOverBlockRetained:
    # Closed by the FIRST fix (2d7fcd07) already; HEAD retains them closed. Their meaningful
    # "was-broken" baseline is pre-#1140 (D_BASE True -> HEAD False).
    @pytest.mark.parametrize("label,cmd", [
        ("locale $\"...\" prose", 'git commit -m $"run %s later"' % BD),
        ("adjacent-concat prose", 'git commit -m "run ""%s"' % BD),
    ])
    @requires_history
    def test_stays_closed_from_pre_baseline(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: expected a pre-#1140 over-block (non-vacuity): %r" % (label, cmd)
        assert D(cmd) is False, "%s: must stay closed at HEAD: %r" % (label, cmd)


# --- REMEDIATION FAITHFUL CONTROLS — forms that were NEVER a bug (stay False everywhere).
class TestRemediationFaithfulControls:
    @pytest.mark.parametrize("label,cmd", [
        # escaped-dq inside a dq message: the dq arm `"(?:[^"\\]|\\.)*"` always handled `\"`,
        # so this was False at EVERY baseline — a faithful control, NOT an over-block closure.
        ("escaped-dq prose", 'git commit -m "say \\"%s\\""' % BD),
        ("locale benign",    'git commit -m $"genuinely benign"'),
        ("esc-idiom benign", "git commit -m 'it'\\''s fine'"),
    ])
    def test_faithful_prose_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: faithful message must never be blocked: %r" % (label, cmd)


# --- FIX-R COMMIT UNDER-BLOCK (esc/ANSI + executing tail + trailing quoted leg).
class TestFixRCommitUnderBlock:
    # The FIX-R defect (commit). Three-point non-vacuity: D_BASE=True (f6e3639a's narrow strip
    # caught it) / D_FIRSTFIX=False (2d7fcd07 INTRODUCED the leg-merge under-block — the SHARP
    # discriminator) / HEAD=True (FIX-R's bash-faithful body closed it). All 3 separators, WITH
    # the trailing quoted leg (load-bearing — see TestNoTrailingQuotedLegNotTheDefect).
    @pytest.mark.parametrize("sep,tail", [("&&", BD), (";", M5), ("|", BDR)])
    @pytest.mark.parametrize("mlabel,msg", [("esc-idiom", _ESC_MSG), ("ANSI-C", _ANSI_MSG)])
    @requires_history
    def test_commit_underblock_closed_by_fixr(self, sep, tail, mlabel, msg):
        cmd = "git commit -m %s %s %s %s git commit -m 'x'" % (msg, sep, tail, sep)
        assert D_BASE(cmd) is True, \
            "%s/%s: f6e3639a's narrow strip must catch it (non-vacuity leg 1): %r" % (mlabel, sep, cmd)
        assert D_FIRSTFIX(cmd) is False, \
            "%s/%s: the first fix MUST carry the under-block (the discriminator): %r" % (mlabel, sep, cmd)
        assert D(cmd) is True, \
            "%s/%s: FIX-R must CLOSE the leg-merge under-block: %r" % (mlabel, sep, cmd)


# --- FIX-R TAG UNDER-BLOCK (same shape; carrier-7d gap is PRE-EXISTING).
class TestFixRTagUnderBlock:
    # Discriminator D_BASE (f6e3639a) is False: the carrier-7d naive body predates #1140, so
    # the tag under-block already existed at f6e3639a (and at 2d7fcd07). FIX-R closes it -> HEAD True.
    @pytest.mark.parametrize("sep,tail", [("&&", BD), (";", M5)])
    @pytest.mark.parametrize("mlabel,msg", [("esc-idiom", _ESC_MSG), ("ANSI-C", _ANSI_MSG)])
    @requires_history
    def test_tag_underblock_closed_by_fixr(self, sep, tail, mlabel, msg):
        cmd = "git tag -m %s v1 %s %s %s git tag -m 'x' v2" % (msg, sep, tail, sep)
        assert D_BASE(cmd) is False, \
            "%s/%s: the carrier-7d gap PRE-EXISTS f6e3639a (the discriminator): %r" % (mlabel, sep, cmd)
        assert D(cmd) is True, \
            "%s/%s: FIX-R must CLOSE the tag leg-merge under-block: %r" % (mlabel, sep, cmd)


# --- NO-TRAILING-QUOTED-LEG trigger characterization (why the first-pass matrix missed it).
class TestNoTrailingQuotedLegNotTheDefect:
    # The trailing quoted leg is LOAD-BEARING: without it there is no later `'` for the dangling
    # quote to pair with, so even the flawed first fix CAUGHT these (D_FIRSTFIX=True). This pins
    # WHY the original 26-vector matrix stayed green while a real under-block existed — it never
    # combined esc-idiom/ANSI + executing tail + a trailing quoted leg.
    @pytest.mark.parametrize("mlabel,msg", [("esc-idiom", _ESC_MSG), ("ANSI-C", _ANSI_MSG)])
    @requires_history
    def test_no_tql_was_caught_even_by_first_fix(self, mlabel, msg):
        cmd = "git commit -m %s && %s" % (msg, BD)
        assert D_FIRSTFIX(cmd) is True, \
            "%s: no-TQL was NOT the defect (caught even at the first fix): %r" % (mlabel, cmd)
        assert D(cmd) is True, "%s: still caught at HEAD: %r" % (mlabel, cmd)


# --- UNDER-BLOCK RETENTION under C4's widened anchor (bundled/attached/cluster + tail).
class TestBundledAttachedUnderBlockRetained:
    # C4 widened the anchor; it must NOT open an under-block. FIX-R's body bounds leg-locality
    # INDEPENDENT of the anchor, so an executing tail after a bundled/attached commit/tag stays
    # a separate leg and is caught. Non-vacuity: D_BASE=True (always caught) -> HEAD True.
    @pytest.mark.parametrize("label,cmd", [
        ("commit -am && tail",     'git commit -am "msg" && %s' % BDR),
        ("commit -m\"\" && tail",   'git commit -m"msg" && %s' % BDR),
        ("commit -am ; merge",     'git commit -am "x" ; %s' % M5),
        ("commit -am | bash",      'git commit -am "run %s" | bash' % BD),
        ("tag -am && tail",        'git tag -am "x" v1 && %s' % BDR),
        ("absurd cluster && tail", 'git commit -%sm "x" && %s' % ("a" * 24, BDR)),
    ])
    @requires_history
    def test_bundled_attached_tail_caught(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: was always caught (non-vacuity): %r" % (label, cmd)
        assert D(cmd) is True, "%s: C4 must NOT open an under-block on the executing tail: %r" % (label, cmd)


# --- A3 value-flag-in-cluster safety (-Cm reuse-message, -Fm file).
class TestA3ValueFlagCluster:
    # -Cm/-Fm are value-flag clusters ending in m. C4 may over-match and strip a benign
    # positional (over-block-SAFE — removes non-executed content), but must NEVER absorb an
    # executing tail. This is the load-bearing A3 risk the architect flagged.
    def test_a3_faithful_stays_false(self):
        assert D('git commit -Cm HEAD') is False, "-Cm HEAD (reuse-message) is faithful"
        assert D('git commit -Fm file') is False, "-Fm file (message-from-file) is faithful"

    @pytest.mark.parametrize("label,cmd", [
        ("-Cm HEAD ; merge",   'git commit -Cm HEAD ; %s' % M5),
        ("-Fm file && delete", 'git commit -Fm file && %s' % BDR),
    ])
    @requires_history
    def test_a3_executing_tail_still_caught(self, label, cmd):
        assert D_BASE(cmd) is True and D(cmd) is True, \
            "%s: the executing tail after a value-flag cluster must stay caught: %r" % (label, cmd)


# --- carrier-4 NON-REGRESSION (`--message=` is a different machinery component).
class TestCarrier4NonRegression:
    def test_message_equals_stays_false_all_baselines(self):
        # `--message=` (equals form) is carrier-4's variable-assignment strip, ORTHOGONAL to the
        # C4 anchor. It was False before #1140 and the anchor widening must not break it.
        cmd = 'git commit --message="run %s later"' % BD
        assert D(cmd) is False, "carrier-4 --message= must stay False at HEAD: %r" % cmd
        if D_BASE is not None:
            assert D_BASE(cmd) is False and D_FIRSTFIX(cmd) is False and D_FIXR(cmd) is False, \
                "carrier-4 --message= must be non-regressed across ALL baselines: %r" % cmd


# --- C4 ANCHOR char-class DRIFT-DETECTOR (design-intent, deliberately NOT brittle).
class TestC4AnchorDriftDetector:
    def test_anchor_uses_m_excluding_class(self):
        # DESIGN-INTENT DRIFT-DETECTOR — the char class `[a-ln-zA-Z]` in C4's bundled-flag anchor
        # arm `-[a-ln-zA-Z]*m` deliberately EXCLUDES lowercase `m`. That exclusion is LOAD-BEARING:
        # it makes the cluster match stop at the message `m` with NO backtracking, so the pattern is
        # UNBOUNDED (closes even the absurd -aa..am cluster — zero residual over-block) AND PROVABLY
        # LINEAR. A greedy `-[A-Za-z]*m` / `-[a-zA-Z]*m` (INCLUDING m) backtracks to "find" the m ->
        # O(n^2) = a ReDoS over-block-BY-TIMEOUT; a bounded `-[a-ln-zA-Z]{0,N}m` would leave an
        # absurd-cluster residual over-block. A future reader must NOT "simplify"/bound this class.
        anchor = r"((?:--message|-[a-ln-zA-Z]*m)\s*)"
        src = Path(mgc.__file__).read_text()
        assert src.count(anchor) >= 2, \
            "the C4 m-excluding anchor must appear at BOTH the commit AND tag carriers; found %d" % src.count(anchor)
        assert r"-[A-Za-z]*m" not in src and r"-[a-zA-Z]*m" not in src, \
            "the anchor must NOT use an m-INCLUDING class (backtracking -> ReDoS/over-block-by-timeout)"
        assert r"-[a-ln-zA-Z]{0," not in src, \
            "the anchor cluster must be UNBOUNDED (a bounded {0,N} leaves an absurd-cluster residual over-block)"


# --- ReDoS LINEARITY pins (ReDoS is an over-block BY TIMEOUT).
def _elapsed_ms(cmd):
    import time
    start = time.perf_counter()
    D(cmd)
    return (time.perf_counter() - start) * 1000.0


class TestReDoSLinearity:
    # A hang blocks a faithful click -> ReDoS is an over-block. Both the C4 anchor cluster and
    # the FIX-R body must be linear. Bounds are generous vs a linear impl (single-digit ms even
    # at n=8000) but a backtracking regression (O(n^2)+) blows them by orders of magnitude.
    @pytest.mark.parametrize("n", [2000, 8000])
    def test_c4_anchor_cluster_linear(self, n):
        # A huge VALID bundled cluster AND a pathological NO-`m` cluster (the input that forces a
        # greedy m-including variant to backtrack the whole run). C4 handles both in linear time.
        assert _elapsed_ms('git commit -%sm "run %s"' % ("a" * n, BD)) < 1000.0, "valid cluster n=%d" % n
        assert _elapsed_ms('git commit -%sx "run %s"' % ("a" * n, BD)) < 1000.0, "no-m cluster n=%d" % n

    @pytest.mark.parametrize("n", [2000, 8000])
    def test_fixr_body_linear(self, n):
        # Pathological quote/escape runs that stress the multi-arm bash-faithful body's ambiguity.
        assert _elapsed_ms('git commit -m ' + '"' * n) < 1000.0, "unclosed-dq run n=%d" % n
        assert _elapsed_ms("git commit -m " + "$'" * n) < 1000.0, "ansi-c open run n=%d" % n
        assert _elapsed_ms('git commit -m ' + '\\' * n) < 1000.0, "backslash run n=%d" % n
