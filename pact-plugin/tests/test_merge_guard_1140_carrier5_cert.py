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

# --- FOLD-ALL-4 baseline (#1176 remediation cycle 2). A FRESH independent adversarial re-review of
#     the FIX-R+C4 HEAD found FOUR MORE pre-existing over-blocks on the message-value strip surface;
#     three fold commits closed them:
#       F3+F4 (span-scoped substitution preserve — _preserve_substitution_spans + _extract_* scanners
#              in the SHARED _keep_carrier_value: preserve only the genuine $()/backtick SPANS, strip
#              the surrounding inert literal, dq-inner apostrophe folded into the literal run),
#       F1    (per-verb message-abbreviation anchors: git commit long arm = any prefix-of-message
#              --m..--message; git tag long arm BOUNDED to --mes..--message so it can never touch the
#              value-taking --merged/--no-merged),
#       F2    (sibling message-carrying git verbs: merge / stash push+store+save / notes add+append;
#              cherry-pick/revert EXCLUDED — their -m is --mainline <NUMBER>, not a message).
#     D_PREFOLD is the SINGLE unifying pre-fold baseline for ALL THREE classes: 3972bb5f is the
#     DIRECT PARENT of the first fold commit (== 6a3a86b7^) and, being a TEST-ONLY commit (the 88-test
#     cert expansion above), its merge_guard_common.py is byte-identical to the C4 source — so it LACKS
#     _preserve_substitution_spans / _MSG_FLAG_ANCHOR / the --m(?:e...)? long arm and every fold
#     over-block still reproduces there. THE NON-VACUITY CRUX: each fold OVER-BLOCK-CLOSURE row asserts
#     D_PREFOLD(cmd) is True -> D(cmd) is False (the flip that proves the fix is load-bearing). A vector
#     that is False at BOTH proves nothing about the fold and is codified as a CONTROL instead — see the
#     `--m` boundary control (TestF1AbbreviationControls): `--m` ends exactly at the message `m`, so the
#     UNCHANGED C4 short arm `-[a-ln-zA-Z]*m` already stripped its value pre-fold; the genuine closure
#     set begins at `--me` (a trailing char after the `m` is what breaks the short arm). This boundary
#     was found EMPIRICALLY by ground-truthing every vector's {pre-fold, HEAD} polarity before codifying.
_PREFOLD_SHA = "3972bb5f"  # 6a3a86b7^ — FIX-R+C4 HEAD, source byte-identical to f7f370a3 (pre-fold)
_PREFOLD = _load_classifier(_PREFOLD_SHA)
D_PREFOLD = _PREFOLD.is_dangerous_command if _PREFOLD is not None else None
requires_prefold = pytest.mark.skipif(
    _PREFOLD is None,
    reason="fold-all-4 pre-fold differential requires merged history (shallow clone / missing 3972bb5f)",
)

# --- CARRIER-4 EQUALS-FORM baseline (#1176 remediation cycle 3). The final adversarial re-review's
#     completeness critic caught an attached-equals over-block: a double-quoted variable-assignment
#     value (an attached-equals flag `--message=`/`--title=`/… OR a general `FOO=`) carrying a benign
#     command-substitution beside danger-looking prose was reverted WHOLE by carrier-4's _strip_var_dq
#     (its old `if _has_command_substitution: return whole`), so the danger prose survived -> over-block.
#     Fix 00195c1a routes such a value through the SAME _preserve_substitution_spans scanner the message
#     carriers use (strip the inert literal, preserve the genuine $()/backtick spans), with two guards
#     kept ahead of it: _var_is_expanded FIRST (a bare $VAR expansion executes the WHOLE value), and a
#     `.`-guard (the matched key is preceded by `.` -> a `git -c section.key=` config injection, whose
#     literal is not provably inert -> preserve WHOLE, keeping the #11 config-injection surface at EXACT
#     status quo). D_PREFIX_C4 = bf7c8786 is the DIRECT PARENT of the fix (== 00195c1a^) and the SHARPEST
#     immediate pre-fix baseline: it isolates THIS fix (carrier-4's pre-fix whole-preserve _strip_var_dq),
#     where every equals-form over-block still reproduces. Each equals-form OVER-BLOCK-CLOSURE row asserts
#     D_PREFIX_C4(cmd) is True -> D(cmd) is False. (D_PREFOLD=3972bb5f carries the same over-block — carrier-4
#     was byte-identical there — but conflates it with the whole remediation arc; bf7c8786 is the per-fix
#     discriminator.) Polarities were EMPIRICALLY ground-truthed at both baselines before codifying.
_PREFIX_C4_SHA = "bf7c8786"  # 00195c1a^ — immediate pre-fix parent (span-scope not yet in _strip_var_dq)
_PREFIX_C4 = _load_classifier(_PREFIX_C4_SHA)
D_PREFIX_C4 = _PREFIX_C4.is_dangerous_command if _PREFIX_C4 is not None else None
requires_prefix_c4 = pytest.mark.skipif(
    _PREFIX_C4 is None,
    reason="carrier-4 equals-form pre-fix differential requires merged history (shallow clone / missing bf7c8786)",
)

# --- F-C1 4-CARRIER baseline (#1176 remediation cycle 4 — the FINAL coarse-substitution-preserve cure).
#     The adversarial re-review found the same benign-$()-preserves-whole pathology latent in the LAST 4
#     prose carriers that had NOT yet been span-scoped: C3 echo/printf (_strip_echo_dq), C6 here-string
#     (_strip_herestring_dq), C8 HTTP-body -d/--data (_keep_flag_dq), C9 gh-api -q/--jq selector
#     (_keep_selector_dq) — each reverted the WHOLE dq value on ANY $()/backtick, so a benign $(date)
#     beside danger prose survived -> over-block. Fix 3f25c8ea routes all four through the SAME
#     _preserve_substitution_spans scanner, emitting the scanner's NATIVE DOUBLE-QUOTED output. That
#     native-dq output is load-bearing: C8/C9 emit single-quoted 'STRIPPED' for a no-$() value but a
#     DOUBLE-quoted "...STRIPPED..." for a has-$() value, keeping each preserved span in the SAME
#     executing dq context the coarse preserve used — so a REAL $(malicious) is detected IDENTICALLY to
#     base (verified True==base on every per-carrier true-positive below; a TP flipping to False would be
#     a security under-block, not codified around). Each carrier's existing guards (outer piped/procsub
#     skip; C6 shell-preceding bash/sh/zsh) stay AHEAD of the span-scope; sq carriers are untouched. This
#     closes the coarse-preserve class across ALL 6 carriers (message via F3, var-assignment via the
#     equals-form fix, + these 4). D_PREFIX_FC1 = a62703f1 is the DIRECT PARENT of the fix (== 3f25c8ea^)
#     — the equals-form CERT commit whose merge_guard_common.py source is the post-equals-form / pre-F-C1
#     classifier where all 4 carriers still coarse-preserve — the SHARP per-fix discriminator. Each closure
#     asserts D_PREFIX_FC1(cmd) is True -> D(cmd) is False. Polarities EMPIRICALLY ground-truthed first.
_PREFIX_FC1_SHA = "a62703f1"  # 3f25c8ea^ — pre-F-C1 classifier (the 4 carriers still coarse-preserve)
_PREFIX_FC1 = _load_classifier(_PREFIX_FC1_SHA)
D_PREFIX_FC1 = _PREFIX_FC1.is_dangerous_command if _PREFIX_FC1 is not None else None
requires_prefix_fc1 = pytest.mark.skipif(
    _PREFIX_FC1 is None,
    reason="F-C1 4-carrier pre-fix differential requires merged history (shallow clone / missing a62703f1)",
)

# --- ECHO/PRINTF CARVE-OUT baseline (#1176 remediation cycle 5b — the bounded echo/printf multi-arg fold).
#     The anchor-coverage audit found that carrier-3 (echo/printf) matched ONLY the FIRST quoted arg, so a
#     danger literal in a 2nd+ positional arg was never stripped -> over-block (the boundary this cert pinned
#     in cycle 4). The user chose a BOUNDED echo/printf-only carve-out (e2145b44): echo/printf PROVABLY never
#     execute their positional args (they PRINT them), so the carrier now strips EVERY quoted positional arg
#     (dq span-scoped via _preserve_substitution_spans, sq -> bareword) inside the piped/procsub skip, with
#     leg-locality keeping an executing tail outside the span. The broader positional over-block surface in
#     commands that DO execute args (C5 git-trailer/author, C7 gh non-carrier flags, C8 curl non-body flags,
#     + the general shape) is DEFERRED to a dedicated audit (see TestEchoPrintfDeferredBoundary). D_PREFIX_ECHO
#     = a542e21b is the DIRECT PARENT of the carve-out (== e2145b44^) — the F-C1 cert commit whose source is
#     the post-F-C1 pre-carve-out classifier where echo/printf still strips only the first arg — the SHARP
#     per-fix discriminator. Each closure asserts D_PREFIX_ECHO(cmd) is True -> D(cmd) is False. EMPIRICALLY
#     ground-truthed (a single-arg backtick case was reclassified OUT — it was already closed by F-C1, so it
#     is False==False vs this baseline, NOT a carve-out closure; only 2nd+ arg cases flip here).
_PREFIX_ECHO_SHA = "a542e21b"  # e2145b44^ — post-F-C1 pre-carve-out (echo/printf strips only the 1st arg)
_PREFIX_ECHO = _load_classifier(_PREFIX_ECHO_SHA)
D_PREFIX_ECHO = _PREFIX_ECHO.is_dangerous_command if _PREFIX_ECHO is not None else None
requires_prefix_echo = pytest.mark.skipif(
    _PREFIX_ECHO is None,
    reason="echo/printf carve-out pre-fix differential requires merged history (shallow clone / missing a542e21b)",
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
        # DESIGN-INTENT DRIFT-DETECTOR — the char class `[a-ln-zA-Z]` in the bundled-flag SHORT ARM
        # `-[a-ln-zA-Z]*m` deliberately EXCLUDES lowercase `m`. That exclusion is LOAD-BEARING:
        # it makes the cluster match stop at the message `m` with NO backtracking, so the pattern is
        # UNBOUNDED (closes even the absurd -aa..am cluster — zero residual over-block) AND PROVABLY
        # LINEAR. A greedy `-[A-Za-z]*m` / `-[a-zA-Z]*m` (INCLUDING m) backtracks to "find" the m ->
        # O(n^2) = a ReDoS over-block-BY-TIMEOUT; a bounded `-[a-ln-zA-Z]{0,N}m` would leave an
        # absurd-cluster residual over-block. A future reader must NOT "simplify"/bound this class.
        #
        # REALIGNED for the fold (F1): the positive check pins the m-EXCLUDING SHORT ARM
        # `-[a-ln-zA-Z]*m` — the load-bearing linear/unbounded invariant — NOT the full anchor string.
        # F1 legitimately rewrote each carrier's LONG arm (commit -> the prefix-of-message
        # `--m(?:e(?:s...)?)?`; tag -> the bounded `--mes(?:s...)?`), leaving the short arm UNCHANGED,
        # so the OLD full-string pin `((?:--message|-[a-ln-zA-Z]*m)\s*)` now appears 0x and its
        # positive assertion was a STALE incidental over-coupling. This detector owns the short-arm
        # class ONLY; the long arm's abbreviation-closure + --merged/--no-merged over-strip-safety is
        # guarded BEHAVIORALLY by the F1 vector classes (TestF1CommitAbbreviationClosure /
        # TestF1TagAbbreviationClosure / TestF1AbbreviationControls), not by a brittle full-string pin.
        # Re-pinning the whole (now per-verb-divergent) anchor here would re-introduce exactly the
        # over-coupling this comment warns against — do NOT. The short arm appears at BOTH the commit
        # AND tag carriers (>= 2; F2's _MSG_FLAG_ANCHOR adds a 3rd, but this detector deliberately does
        # NOT couple to F2's carrier count — >= 2, not >= 3).
        short_arm = r"-[a-ln-zA-Z]*m"
        src = Path(mgc.__file__).read_text()
        assert src.count(short_arm) >= 2, \
            "the m-excluding SHORT ARM must appear at BOTH the commit AND tag carriers; found %d" % src.count(short_arm)
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


# ===========================================================================
# ===  #1176 FOLD-ALL-4 — four re-review over-blocks closed on the message  ===
# ===  value-strip surface (F1 abbreviated --message, F2 sibling verbs,     ===
# ===  F3+F4 span-scoped substitution preserve). NON-VACUITY vs D_PREFOLD    ===
# ===  (3972bb5f = 6a3a86b7^): EVERY over-block-closure flips True(pre-fold) ===
# ===  -> False(HEAD); true-positives / under-block canaries / fail-safe     ===
# ===  residuals hold True at BOTH. Polarities were EMPIRICALLY ground-      ===
# ===  truthed against the real classifier before codification (the --m     ===
# ===  boundary control is a False==False vector reclassified OUT of the     ===
# ===  closure set — a closure that does not flip would be vacuous).         ===
# ===========================================================================

# --- F1 — abbreviated spaced --message anchor (commit prefix-of-message, tag bounded --mes).
class TestF1CommitAbbreviationClosure:
    # git commit's ONLY --m* option is --message, so every --m prefix unambiguously means it. The C4
    # short arm mis-matched the `-m` INSIDE a longer abbreviation (--mess -> --mSTRIPPED, the real
    # quoted value survived -> over-block); F1's prefix-of-message long arm matches the whole
    # abbreviation and strips the value. The closure set BEGINS at --me (--m is the boundary control).
    @pytest.mark.parametrize("label,cmd", [
        ("--me",     'git commit --me "run %s later"' % BD),
        ("--mes",    'git commit --mes "run %s later"' % BD),
        ("--mess",   'git commit --mess "run %s later"' % BD),
        ("--messa",  'git commit --messa "run %s later"' % BD),
        ("--messag", 'git commit --messag "run %s later"' % BD),
        ("--mess sq", "git commit --mess 'run %s later'" % BD),
    ])
    @requires_prefold
    def test_commit_abbrev_closed(self, label, cmd):
        assert D_PREFOLD(cmd) is True, \
            "%s: must be over-blocked at pre-fold (else the closure is vacuous): %r" % (label, cmd)
        assert D(cmd) is False, \
            "%s: F1 must CLOSE the abbreviated-message over-block (cardinal sin if blocked): %r" % (label, cmd)


class TestF1TagAbbreviationClosure:
    # git tag ALSO has value-taking --merged/--no-merged, so its long arm is BOUNDED to --mes..--message
    # (--m/--me are ambiguous — git rejects them). --mes onward unambiguously means --message.
    @pytest.mark.parametrize("label,cmd", [
        ("--mes",   'git tag --mes "run %s later" v1' % BD),
        ("--mess",  'git tag --mess "run %s later" v1' % BD),
        ("--messa", 'git tag --messa "run %s later" v1' % BD),
    ])
    @requires_prefold
    def test_tag_abbrev_closed(self, label, cmd):
        assert D_PREFOLD(cmd) is True, "%s: must be over-blocked at pre-fold: %r" % (label, cmd)
        assert D(cmd) is False, "%s: F1 must CLOSE the tag abbreviated-message over-block: %r" % (label, cmd)


class TestF1AbbreviationControls:
    # Faithful forms that must NEVER be blocked at HEAD. `--m` is the BOUNDARY: it ends exactly at the
    # message `m`, so the UNCHANGED C4 short arm already stripped its value pre-fold (False at BOTH
    # baselines -> a CONTROL, NOT a closure; codifying it as a closure would be VACUOUS). --merged /
    # --no-merged are the over-strip-safety controls: the tag long arm (bounded --mes) can never
    # consume their values (char 3 is r != s), so they stay non-dangerous reads.
    @pytest.mark.parametrize("label,cmd", [
        ("commit --m boundary",  'git commit --m "run %s later"' % BD),
        ("commit --message",     'git commit --message "run %s later"' % BD),
        ("commit -m",            'git commit -m "run %s later"' % BD),
        ("commit --mess benign", 'git commit --mess "just a normal message"'),
        ("tag --message",        'git tag --message "run %s later" v1' % BD),
        ("tag --merged",         'git tag --merged mainbranch'),
        ("tag --no-merged",      'git tag --no-merged mainbranch'),
    ])
    def test_faithful_abbrev_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: a faithful message-abbreviation click must NEVER be blocked: %r" % (label, cmd)

    @requires_prefold
    def test_dashm_boundary_was_false_prefold(self):
        # Documents WHERE the closure set starts: `--m` was NOT over-blocked pre-fold (the C4 short
        # arm already handled it), so it is a control, not a closure — the empirical basis for the
        # closure set beginning at --me. This assertion is the tripwire that keeps the boundary honest.
        assert D_PREFOLD('git commit --m "run %s later"' % BD) is False


class TestF1AbbreviationUnderBlock:
    # F1 widened the anchor; it must NOT open an under-block. An executing destructive tail after an
    # abbreviated-message commit/tag stays a separate leg and is caught. Non-vacuity: True at BOTH.
    @pytest.mark.parametrize("label,cmd", [
        ("commit --mess && tail",  'git commit --mess "ok" && %s' % BDR),
        ("commit --messa ; merge", 'git commit --messa "ok" ; %s' % M5),
        ("tag --mess && tail",     'git tag --mess "ok" v1 && %s' % BDR),
        ("tag --merged && tail",   'git tag --merged mainbranch && %s' % BDR),
    ])
    @requires_prefold
    def test_abbrev_tail_caught(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: the executing tail after an abbreviated-message flag must stay caught: %r" % (label, cmd)

    @requires_prefold
    def test_nonfaithful_multiword_documented(self):
        # `git commit --mess <BD>` UNQUOTED (multi-word) stays True at BOTH — NOT a regression: an
        # unquoted git message is a SINGLE word, so `--mess git` takes `git` as the message and the
        # `-D` makes git itself reject the command. A malformed non-faithful form; documented as
        # True==True (no closure claimed, no under-block opened).
        cmd = 'git commit --mess %s' % BD
        assert D_PREFOLD(cmd) is True and D(cmd) is True, cmd


class TestF1AbbreviationTruePositive:
    @requires_prefold
    def test_abbrev_substitution_still_caught(self):
        # An abbreviated flag carrying a REAL $(destructive) still executes -> stays caught (True both).
        cmd = 'git commit --mess "$(%s)"' % BD
        assert D_PREFOLD(cmd) is True and D(cmd) is True, cmd


# --- F2 — sibling message-carrying git verbs (merge / stash push+store+save / notes add+append).
class TestF2SiblingVerbClosure:
    # Pre-fold has NO carrier for these verbs, so a destructive literal in the message survived ->
    # over-block. F2 adds a span-bounded flag-anchored carrier for each (the same machinery as
    # carriers 5/7d). Non-vacuity: True(pre-fold) -> False(HEAD).
    @pytest.mark.parametrize("label,cmd", [
        ("merge -m",             'git merge -m "run %s later" feat' % BD),
        ("merge --message",      'git merge --message "run %s later" feat' % BD),
        ("merge --mess",         'git merge --mess "run %s later" feat' % BD),
        ("stash push -m",        'git stash push -m "run %s later"' % BD),
        ("stash push --message", 'git stash push --message "run %s later"' % BD),
        ("stash store -m",       'git stash store -m "run %s later" abc123' % BD),
        ("stash save positional", 'git stash save "run %s later"' % BD),
        ("stash save -u",        'git stash save -u "run %s later"' % BD),
        ("notes add -m",         'git notes add -m "run %s later"' % BD),
        ("notes append -m",      'git notes append -m "run %s later"' % BD),
        ("notes --ref add -m",   'git notes --ref refs/notes/x add -m "run %s later"' % BD),
        ("merge -m F2xF3 sub",   'git merge -m "as of $(date): run %s later" feat' % BD),
    ])
    @requires_prefold
    def test_sibling_verb_closed(self, label, cmd):
        assert D_PREFOLD(cmd) is True, \
            "%s: must be over-blocked at pre-fold (no carrier existed): %r" % (label, cmd)
        assert D(cmd) is False, "%s: F2 must CLOSE the sibling-verb message over-block: %r" % (label, cmd)


class TestF2Exclusions:
    # cherry-pick/revert -m is --mainline <parent-number> (a NUMBER, not a message) — DELIBERATELY
    # excluded from the message carriers. Faithful forms are False (no danger); a real destructive
    # tail after them stays caught via leg-locality (True both).
    @pytest.mark.parametrize("label,cmd", [
        ("cherry-pick -m 1", 'git cherry-pick -m 1 abc123'),
        ("revert -m 1",      'git revert -m 1 HEAD'),
    ])
    def test_exclusion_faithful_false(self, label, cmd):
        assert D(cmd) is False, "%s: cherry-pick/revert mainline form is faithful: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("cherry-pick tail", 'git cherry-pick -m 1 abc123 && %s' % BD),
        ("revert tail",      'git revert -m 1 HEAD ; %s' % M5),
    ])
    @requires_prefold
    def test_exclusion_tail_caught(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: the executing tail after an EXCLUDED verb must stay caught: %r" % (label, cmd)


class TestF2BenignControls:
    # Non-message forms + benign messages that must stay False at HEAD (F->F). `git merge-base` must
    # NOT be mis-handled as `git merge`; bare `stash save` (no message) must not misfire.
    @pytest.mark.parametrize("label,cmd", [
        ("merge feat",      'git merge feat'),
        ("merge -m benign", 'git merge -m "just merging main" feat'),
        ("stash list",      'git stash list'),
        ("stash pop",       'git stash pop'),
        ("notes list",      'git notes list'),
        ("merge-base",      'git merge-base main feat'),
        ("stash save bare", 'git stash save'),
        ("stash save wip",  'git stash save wip'),
    ])
    def test_benign_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: benign sibling-verb form must stay False: %r" % (label, cmd)


class TestF2UnderBlock:
    @pytest.mark.parametrize("label,cmd", [
        ("merge -m && tail",   'git merge -m "ok" feat && %s' % BD),
        ("stash push && tail", 'git stash push -m "ok" && %s' % BD),
        ("stash save ; merge", 'git stash save "ok" ; %s' % M5),
        ("notes add | tail",   'git notes add -m "ok" | %s' % BD),
    ])
    @requires_prefold
    def test_sibling_tail_caught(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: the executing tail after a new carrier must stay caught: %r" % (label, cmd)


class TestF2TruePositive:
    @pytest.mark.parametrize("label,cmd", [
        ("merge $(mal)",      'git merge -m "$(%s)" feat' % BD),
        ("stash save $(mal)", 'git stash save "$(%s)"' % BD),
    ])
    @requires_prefold
    def test_sibling_substitution_caught(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: a REAL $(destructive) via a new carrier must stay caught: %r" % (label, cmd)


# --- F3+F4 — span-scoped command-substitution preserve (benign $()/backtick + danger prose).
class TestF3SubstitutionClosure:
    # Pre-fold's _keep_carrier_value preserved the WHOLE value on ANY $()/backtick, so a benign
    # $(date) beside danger-looking prose survived -> over-block. F3 preserves ONLY the genuine
    # substitution SPANS and strips the surrounding inert literal. Non-vacuity: True -> False.
    @pytest.mark.parametrize("label,cmd", [
        ("benign $(date)",             'git commit -m "as of $(date +%%F): stop %s shim"' % PF),
        ("escaped \\$( inert",         'git commit -m "doc \\$(date); note %s"' % BD),
        ("nested $()",                 'git commit -m "build $(echo $(date)) then %s"' % BD),
        ("two-sub",                    'git commit -m "$(date) and $(whoami): %s"' % BD),
        ("backtick benign",            'git commit -m "ran on `hostname`, then %s"' % BD),
        ("apostrophe it's",            'git commit -m "it\'s the $(date) build; note %s"' % BD),
        ("apostrophe don't",           'git commit -m "don\'t $(date): %s"' % BD),
        ("multi-apostrophe",           'git commit -m "it\'s Bob\'s $(date) fix; %s"' % BD),
        ("tag sub",                    'git tag -m "built $(date), see %s" v1' % BD),
        ("escaped-quote OUTside span", 'git commit -m "use \\"$(date)\\" then %s"' % BD),
    ])
    @requires_prefold
    def test_substitution_closed(self, label, cmd):
        assert D_PREFOLD(cmd) is True, \
            "%s: must be over-blocked at pre-fold (whole-value preserve): %r" % (label, cmd)
        assert D(cmd) is False, "%s: F3 must CLOSE the benign-substitution over-block: %r" % (label, cmd)


class TestF3Controls:
    # Faithful forms False at BOTH (F->F). apostrophe-NO-sub is inert prose (no $()) -> stripped at
    # both. benign-sub-no-danger preserves a harmless $(date) at pre-fold (no danger to survive). The
    # single-quoted VALUE is untouched by the fold (arm-2 strips sq unconditionally) — no sq work.
    @pytest.mark.parametrize("label,cmd", [
        ("normal message",       'git commit -m "just a normal message"'),
        ("benign sub no danger", 'git commit -m "the $(date) build"'),
        ("apostrophe NO sub",    'git commit -m "it\'s a fix; drop %s"' % BD),
        ("sq VALUE untouched",   "git commit -m '$(date) then %s'" % BD),
    ])
    def test_faithful_substitution_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: faithful message must never be blocked: %r" % (label, cmd)


class TestF3TruePositive:
    # The scanner PRESERVES $()/backtick spans VERBATIM, so a REAL $(destructive) stays caught.
    @pytest.mark.parametrize("label,cmd", [
        ("whole $(mal)", 'git commit -m "$(%s)"' % BD),
        ("embedded",     'git commit -m "pre $(%s) post"' % BD),
        ("backtick mal", 'git commit -m "`%s`"' % BD),
        ("tag $(mal)",   'git tag -m "$(%s)" v1' % BD),
    ])
    @requires_prefold
    def test_substitution_truepositive_caught(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: a preserved $(destructive) span must stay caught: %r" % (label, cmd)


class TestF3UnderBlock:
    # The span ends at the first UNQUOTED separator, so an executing tail (even after a benign
    # $(date) or an apostrophe value) stays a separate caught leg.
    @pytest.mark.parametrize("label,cmd", [
        ("benign sub + tail",     'git commit -m "ok $(date)" && %s' % BD),
        ("apostrophe val + tail", 'git commit -m "it\'s ok $(date)" && %s' % BD),
        ("benign sub ; merge",    'git commit -m "the $(date) build" ; %s' % M5),
    ])
    @requires_prefold
    def test_substitution_tail_caught(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: the executing tail after a benign substitution must stay caught: %r" % (label, cmd)


class TestF3GhSharedCarrierClosure:
    # _keep_carrier_value is SHARED, so the identical benign-$()+danger over-block latent in the gh
    # issue/pr create carriers is closed by the SAME F3 change (uniform 'model the semantics' fix).
    @pytest.mark.parametrize("label,cmd", [
        ("gh issue create", 'gh issue create --title "as of $(date): %s"' % BD),
        ("gh pr create",    'gh pr create --title "$(date) release; drop %s"' % BD),
    ])
    @requires_prefold
    def test_gh_carrier_substitution_closed(self, label, cmd):
        assert D_PREFOLD(cmd) is True, "%s: must be over-blocked at pre-fold: %r" % (label, cmd)
        assert D(cmd) is False, "%s: F3 must CLOSE the shared gh-carrier over-block: %r" % (label, cmd)

    @requires_prefold
    def test_gh_carrier_truepositive_kept(self):
        cmd = 'gh issue create --title "$(%s)"' % BD
        assert D_PREFOLD(cmd) is True and D(cmd) is True, "gh-carrier $(destructive) must stay caught: %r" % cmd


class TestF3FailSafeResidual:
    # The DOCUMENTED fail-safe residual (design §3.3): a quote-bearing $(...) span coexisting with
    # danger prose in ONE dq value makes _preserve_substitution_spans return None -> preserve the
    # WHOLE value (today's behavior). True==base — NOT a new over-block (== pre-fold) and NOT an
    # under-block (preserve is maximally-caught). BOTH escaped and raw inner-quote forms.
    @pytest.mark.parametrize("label,cmd", [
        ("escaped inner-quote span", 'git commit -m "fix $(basename \\"$d\\") then %s"' % BD),
        ("raw inner-quote span",     'git commit -m "fix $(basename "$d") then %s"' % BD),
    ])
    @requires_prefold
    def test_failsafe_residual_true_at_both(self, label, cmd):
        assert D_PREFOLD(cmd) is True and D(cmd) is True, \
            "%s: the fail-safe residual must be True==base (no regression, no under-block): %r" % (label, cmd)


# --- FOLD STRIP-SURFACE MECHANISM PINS — document HOW each fold closure/retention happens.
class TestFoldStripSurfaceMechanism:
    def test_f1_abbrev_value_stripped(self):
        # F1: the long arm matches the whole abbreviation, so the value strips to the inert bareword.
        assert STRIP('git commit --me "run %s later"' % BD) == "git commit --me STRIPPED"
        assert STRIP('git commit --messag "run %s later"' % BD) == "git commit --messag STRIPPED"

    def test_dashm_boundary_stripped_at_head(self):
        # The `--m` boundary control: the long arm matches --m and strips the value at HEAD too -> False.
        assert STRIP('git commit --m "run %s later"' % BD) == "git commit --m STRIPPED"

    def test_tag_merged_cosmetic_strip(self):
        # Lead pin: the UNCHANGED short arm cosmetically strips --merged -> --mSTRIPPED, but the
        # positional survives and the command stays a non-dangerous read (False). Documented, harmless.
        assert STRIP('git tag --merged mainbranch') == "git tag --mSTRIPPED mainbranch"
        assert STRIP('git tag --no-merged mainbranch') == "git tag --no-mSTRIPPED mainbranch"

    def test_f2_save_positional_stripped(self):
        assert STRIP('git stash save "run %s later"' % BD) == "git stash save STRIPPED"
        assert STRIP('git stash save -u "run %s later"' % BD) == "git stash save -u STRIPPED"

    def test_f2_mergebase_not_mishandled(self):
        # `git merge-base` must survive intact (no message flag for the carrier to anchor on).
        assert STRIP('git merge-base main feat') == "git merge-base main feat"

    def test_f3_span_scoped_strip(self):
        # F3: inert literal -> STRIPPED, the benign span preserved VERBATIM.
        assert STRIP('git commit -m "use \\"$(date)\\" then %s"' % BD) == 'git commit -m "STRIPPED$(date)STRIPPED"'

    def test_f3_residual_whole_value_preserved(self):
        # Fail-safe residual: the whole value is preserved (danger survives -> stays caught), == pre-fold.
        s = STRIP('git commit -m "fix $(basename \\"$d\\") then %s"' % BD)
        assert BD in s, "the fail-safe residual must preserve the whole value (danger stays visible): %r" % s


# --- FOLD ReDoS LINEARITY pins (ReDoS is an over-block BY TIMEOUT). The F3 scanners + the F2 save
#     flag-run must be linear; the measured cost is ~50 ms at n=16000 (bounds are generous vs that).
class TestFoldReDoSLinearity:
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_f3_scanner_linear(self, n):
        assert _elapsed_ms('git commit -m "' + "$(" * n + '"') < 1000.0, "unterminated $( n=%d" % n
        assert _elapsed_ms('git commit -m "$(' + "(" * n + ")" * n + ')"') < 1000.0, "nested parens n=%d" % n
        assert _elapsed_ms('git commit -m "' + "'" * n + '$(date)"') < 1000.0, "apostrophe-run n=%d" % n
        assert _elapsed_ms('git commit -m "' + "$" * n + '"') < 1000.0, "dollar-run n=%d" % n

    @pytest.mark.parametrize("n", [4000, 16000])
    def test_f2_save_flag_run_linear(self, n):
        # The stash-save positional anchor `(save(?:\s+-[-\w]+)*\s+)` is a deterministic per-flag loop.
        assert _elapsed_ms('git stash save ' + "-a " * n + '"msg"') < 1000.0, "save flag-run n=%d" % n


# ===========================================================================
# ===  #1176 CARRIER-4 EQUALS-FORM — remediation cycle 3. _strip_var_dq now  ===
# ===  routes a non-expanded, non-config NAME="...$()..." value through the   ===
# ===  SAME span scanner the message carriers use (_var_is_expanded FIRST +   ===
# ===  a `.`-guard keeping git -c section.key= config injections at status    ===
# ===  quo). NON-VACUITY vs D_PREFIX_C4 (bf7c8786 = 00195c1a^): every         ===
# ===  equals-form closure flips True(pre-fix) -> False(HEAD). True-positives, ===
# ===  the `-c` status-quo pins, and expanded/eval hold True at BOTH; the      ===
# ===  no-$() literals / sq forms hold False at BOTH. Polarities EMPIRICALLY   ===
# ===  ground-truthed (bf7c8786 AND 3972bb5f) before codification.            ===
# ===========================================================================

class TestC4EqualsFormClosure:
    # The attached-equals over-block the completeness critic caught: a dq variable-assignment value
    # (attached-equals flag OR general FOO=) carrying a benign $()/backtick beside danger prose was
    # reverted WHOLE at pre-fix -> danger survived. The span-scope strips the inert literal and
    # preserves only the genuine substitution span. Non-vacuity: True(bf7c8786) -> False(HEAD).
    @pytest.mark.parametrize("label,cmd", [
        ("commit --message=",   'git commit --message="as of $(date): note %s"' % BD),
        ("commit -m=",          'git commit -m="$(date) note %s"' % BD),
        ("commit backtick=",    'git commit --message="ran on `hostname`, then %s"' % BD),
        ("commit apostrophe=",  'git commit --message="it\'s $(date); %s"' % BD),
        ("commit two-sub=",     'git commit --message="$(date) and $(whoami): %s"' % BD),
        ("gh issue --title=",   'gh issue create --title="as of $(date): %s"' % BD),
        ("gh pr --body=",       'gh pr create --body="$(date) note %s"' % BD),
        ("gh release --notes=", 'gh release create v1 --notes="$(date) drop %s"' % BD),
        ("gh gist --desc=",     'gh gist create --desc="$(date) note %s" f.txt' % BD),
        ("merge --message=",    'git merge --message="$(date) %s" feat' % BD),
        ("stash push --message=", 'git stash push --message="$(date) %s"' % BD),
        ("notes add --message=", 'git notes add --message="$(date) %s"' % BD),
        ("general FOO=",        'FOO="$(date) note %s"' % BD),
    ])
    @requires_prefix_c4
    def test_equals_form_closed(self, label, cmd):
        assert D_PREFIX_C4(cmd) is True, \
            "%s: must be over-blocked at pre-fix bf7c8786 (else the closure is vacuous): %r" % (label, cmd)
        assert D(cmd) is False, \
            "%s: the fix must CLOSE the equals-form over-block (cardinal sin if blocked): %r" % (label, cmd)


class TestC4EquivalenceAxis:
    # The under-block-safety REDUCES to carrier-4's accepted no-$() status quo (design §4.1):
    #   (i)  no-$() literal        -> stripped WHOLE -> False  [carrier-4 ALREADY did this]
    #   (ii) $()+literal           -> literal stripped, span preserved -> False  [SAME disposition
    #        on the literal as (i), + the span] -> a CLOSURE (True pre-fix -> False HEAD)
    #   (iii) expanded / eval      -> preserve WHOLE -> True  [_var_is_expanded/_has_eval FIRST]
    # Because (ii) gives the literal the identical disposition as (i), the fix adds ZERO new
    # under-block beyond carrier-4's accepted status quo.
    @pytest.mark.parametrize("label,cmd", [
        ("(i) FOO= no-$()",       'FOO="just note %s"' % BD),
        ("(i) --message= no-$()", 'git commit --message="just note %s"' % BD),
    ])
    def test_axis_i_nosub_literal_false_both(self, label, cmd):
        # (i) status quo: carrier-4's no-$() strip removes the whole inert literal at EVERY baseline.
        assert D(cmd) is False, "%s: no-$() literal must be stripped -> False: %r" % (label, cmd)
        if D_PREFIX_C4 is not None:
            assert D_PREFIX_C4(cmd) is False, "%s: (i) must be False at pre-fix too (status quo): %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("(ii) FOO= $()+literal",       'FOO="$(date) just note %s"' % BD),
        ("(ii) --message= $()+literal", 'git commit --message="$(date) just note %s"' % BD),
    ])
    @requires_prefix_c4
    def test_axis_ii_sub_plus_literal_closes(self, label, cmd):
        # (ii): the literal gets the SAME disposition as (i) (stripped) + the span preserved -> False.
        # It was an over-block at pre-fix (whole-value revert) -> a genuine closure.
        assert D_PREFIX_C4(cmd) is True, "%s: must be over-blocked at pre-fix: %r" % (label, cmd)
        assert D(cmd) is False, "%s: (ii) literal must strip to the SAME False as (i): %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("(iii) expanded no-$()",  'FOO="drop %s" && $FOO' % BD),
        ("(iii) expanded has-$()", 'FOO="$(date) %s" && $FOO' % BD),
        ("(iii) eval no-$()",      'FOO="drop %s" ; eval $FOO' % BD),
        ("(iii) eval has-$()",     'FOO="$(date) %s" ; eval $FOO' % BD),
    ])
    @requires_prefix_c4
    def test_axis_iii_expanded_eval_true_both(self, label, cmd):
        # (iii): a bare $VAR expansion / eval executes the WHOLE value -> preserve whole -> caught at
        # BOTH baselines (the _var_is_expanded / _has_eval-FIRST guards, unchanged by the fix).
        assert D_PREFIX_C4(cmd) is True and D(cmd) is True, \
            "%s: expanded/eval must preserve-whole -> caught at both: %r" % (label, cmd)


class TestC4TruePositive:
    # The scanner PRESERVES $()/backtick spans VERBATIM (they execute at assignment), and an executing
    # tail is a separate caught leg (leg-locality). True == pre-fix.
    @pytest.mark.parametrize("label,cmd", [
        ("commit =$(mal)",    'git commit --message="$(%s)"' % BD),
        ("FOO=$(mal)",        'FOO="$(%s)"' % BD),
        ("FOO=backtick",      'FOO="`%s`"' % BD),
        ("gh --title=$(mal)", 'gh issue create --title="$(%s)"' % BD),
        ("FOO= + && tail",    'FOO="note wip" && %s' % BD),
        ("=value + && tail",  'git commit --message="ok $(date)" && %s' % BD),
    ])
    @requires_prefix_c4
    def test_equals_truepositive_caught(self, label, cmd):
        assert D_PREFIX_C4(cmd) is True and D(cmd) is True, \
            "%s: a real $(destructive)/executing tail must stay caught at both: %r" % (label, cmd)


class TestC4DotGuardStatusQuo:
    # `git -c section.key=value` config-injection (#11) is OUT OF SCOPE: a config value can be an
    # EXECUTABLE config (core.pager/alias.*/core.editor), so the `.`-guard (matched key preceded by
    # `.`) preserves the WHOLE value, keeping the -c surface at EXACT status quo. Every `-c` case is
    # PATCH == BASE (the fix neither improves nor weakens -c detection). Empirically ground-truthed:
    # the guard keys on match.string[start-1]=='.' over the POST-carrier-1..3 sub surface, and on a
    # `-c user.name=` the char before `name` IS `.`, while on `--message=`/`FOO=` it is `-`/nothing.
    @pytest.mark.parametrize("label,cmd,expected", [
        ("residual over-block", 'git -c user.name="$(date) %s" commit' % BD, True),   # deliberate `.`-guard trade
        ("benign sub",          'git -c user.name="$(date)" commit', False),
        ("#11 gap no-$()",      'git -c core.pager="%s" commit' % BD, False),          # carrier-4 no-$() strip
        ("preserved $(danger)", 'git -c core.pager="$(%s)" commit' % BD, True),
    ])
    @requires_prefix_c4
    def test_c_config_patch_equals_base(self, label, cmd, expected):
        assert D(cmd) is expected, "%s: -c status-quo expected %s at HEAD: %r" % (label, expected, cmd)
        assert D_PREFIX_C4(cmd) is expected, \
            "%s: -c config detection must be PATCH==BASE (status quo, no change): %r" % (label, cmd)


class TestC4SqControls:
    # Single-quoted assignments are UNAFFECTED by the fix (the sq strip is unconditional inside the
    # non-expanded branch; sq $() is a literal, never executed). False at HEAD (and at pre-fix).
    @pytest.mark.parametrize("label,cmd", [
        ("--message= sq", "git commit --message='$(date):%s'" % BD),
        ("FOO= sq",       "FOO='$(date) %s'" % BD),
    ])
    def test_sq_equals_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: single-quoted assignment (sq $() is literal) must stay False: %r" % (label, cmd)


class TestC4StripSurfaceMechanism:
    # Document HOW each disposition happens on the real strip surface (so a mechanism regression is
    # visible, not just a boolean flip).
    def test_equals_span_scoped(self):
        assert STRIP('git commit --message="as of $(date): note %s"' % BD) \
            == 'git commit --message="STRIPPED$(date)STRIPPED"'

    def test_general_foo_span_scoped(self):
        assert STRIP('FOO="$(date) note %s"' % BD) == 'FOO="$(date)STRIPPED"'

    def test_nosub_literal_stripped_whole(self):
        assert STRIP('FOO="just note %s"' % BD) == "FOO=STRIPPED"

    def test_dot_guard_preserves_config_value_whole(self):
        # The `.`-guard preserves the WHOLE -c config value (danger stays visible -> caught == base).
        s = STRIP('git -c user.name="$(date) %s" commit' % BD)
        assert BD in s, "the `.`-guard must preserve the whole -c value (status quo): %r" % s
        assert s == 'git -c user.name="$(date) %s" commit' % BD

    def test_nondot_flag_is_span_scoped_not_guarded(self):
        # The `.`-guard must NOT fire on a `--flag=` name (preceded by `-`, not `.`) -> span-scoped.
        # Leading $(date) span preserved, trailing inert literal -> STRIPPED (no leading literal here).
        assert STRIP('git commit --message="$(date) %s"' % BD) == 'git commit --message="$(date)STRIPPED"'

    def test_c_no_sub_config_stripped_11_gap(self):
        # #11 gap unchanged: a -c config value with NO $() is stripped by carrier-4's no-$() arm.
        assert STRIP('git -c core.pager="%s" commit' % BD) == "git -c core.pager=STRIPPED commit"


class TestC4ReDoSLinearity:
    # The reused _preserve_substitution_spans scanner is O(n) and the `.`-guard is O(1); a light
    # linearity pin on the equals-form var-assignment path (measured ~73 ms at n=16000).
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_equals_form_path_linear(self, n):
        assert _elapsed_ms('FOO="' + "$(" * n + '"') < 1000.0, "FOO= unterminated $( n=%d" % n
        assert _elapsed_ms('FOO="$(' + "(" * n + ")" * n + ')"') < 1000.0, "FOO= nested parens n=%d" % n
        assert _elapsed_ms('FOO="' + "'" * n + '$(date)"') < 1000.0, "FOO= apostrophe-run n=%d" % n


# ===========================================================================
# ===  #1176 F-C1 4-CARRIER CURE — remediation cycle 4 (the FINAL           ===
# ===  coarse-substitution-preserve closure). The last 4 prose carriers      ===
# ===  (C3 echo/printf, C6 here-string, C8 HTTP-body -d/--data, C9 gh-api     ===
# ===  -q/--jq) now span-scope has-substitution values via                   ===
# ===  _preserve_substitution_spans (NATIVE DQ output). NON-VACUITY vs        ===
# ===  D_PREFIX_FC1 (a62703f1 = 3f25c8ea^): every closure flips True(pre-fix) ===
# ===  -> False(HEAD); every per-carrier TRUE-POSITIVE holds True==base (the  ===
# ===  load-bearing native-dq under-block check — a TP->False would be a      ===
# ===  security under-block); controls hold False==False. The echo/printf     ===
# ===  MULTI-ARG anchor gap is pinned as a PRE-EXISTING out-of-scope boundary ===
# ===  (True==base, over-blocks even with NO substitution). EMPIRICALLY       ===
# ===  ground-truthed at a62703f1 vs HEAD before codification.               ===
# ===========================================================================

_FC1_URL = "http://example.com/api"


class TestFC1EchoPrintfCarrier:
    # C3: the echo/printf dq argument is PRINTED (never executed); only the $()/backtick span executes
    # at command-build time and is preserved. Routed-to-shell (| bash / >(bash)) is skipped by the outer
    # piped/procsub guard (unchanged). Closure set: benign substitution/backtick + danger prose.
    @pytest.mark.parametrize("label,cmd", [
        ("echo $()+danger",   'echo "as of $(date): note %s"' % BD),
        ("printf $()+danger",  'printf "as of $(date): note %s"' % BD),
        ("echo backtick",     'echo "ran on `hostname`, then %s"' % BD),
        ("echo apostrophe",   'echo "it\'s $(date); %s"' % BD),
        ("echo two-sub",      'echo "$(date) $(whoami): %s"' % BD),
    ])
    @requires_prefix_fc1
    def test_closure(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True, "%s: must be over-blocked at pre-fix (else vacuous): %r" % (label, cmd)
        assert D(cmd) is False, "%s: F-C1 must CLOSE the echo/printf coarse-substitution over-block: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("echo $(mal)",       'echo "$(%s)"' % BD),
        ("echo embedded",     'echo "pre $(%s) post"' % BD),
        ("echo backtick mal", 'echo "`%s`"' % BD),
        ("echo + tail",       'echo "ok $(date)" && %s' % BD),
    ])
    @requires_prefix_fc1
    def test_truepositive(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True and D(cmd) is True, \
            "%s: a real substitution / executing tail must stay caught at BOTH (native-dq): %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("benign sub",   'echo "release $(date +%F)"'),
        ("no sub",       'echo "just a note"'),
        ("sq untouched", "echo '$(date) %s'" % BD),
    ])
    def test_control(self, label, cmd):
        assert D(cmd) is False, "%s: benign/sq echo must stay False: %r" % (label, cmd)


class TestFC1HereStringCarrier:
    # C6: the here-string value feeds the command's STDIN (inert) UNLESS the command IS a shell
    # interpreter -> the shell-preceding guard (bash/sh/zsh) preserves whole in that case (a TP). Only
    # the $() span executes at build time and is preserved.
    @pytest.mark.parametrize("label,cmd", [
        ("herestr $()+danger", 'cat <<< "as of $(date): note %s"' % BD),
        ("herestr backtick",   'cat <<< "ran `hostname`: %s"' % BD),
    ])
    @requires_prefix_fc1
    def test_closure(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True, "%s: must be over-blocked at pre-fix (else vacuous): %r" % (label, cmd)
        assert D(cmd) is False, "%s: F-C1 must CLOSE the here-string coarse-substitution over-block: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("herestr $(mal)",       'cat <<< "$(%s)"' % BD),
        ("herestr embedded",     'cat <<< "pre $(%s) post"' % BD),
        ("herestr shell-preceding", 'bash <<< "$(date) %s"' % BD),  # shell reads stdin -> preserve whole -> True
        ("herestr + tail",       'cat <<< "ok $(date)" ; %s' % BD),
    ])
    @requires_prefix_fc1
    def test_truepositive(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True and D(cmd) is True, \
            "%s: real substitution / shell-preceding / tail must stay caught at BOTH: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("no sub",       'cat <<< "just a note"'),
        ("sq untouched", "cat <<< '$(date) %s'" % BD),
    ])
    def test_control(self, label, cmd):
        assert D(cmd) is False, "%s: benign/sq here-string must stay False: %r" % (label, cmd)


class TestFC1CurlDataCarrier:
    # C8: the -d/--data value is request DATA sent over the network (not executed locally); only the
    # $() span executes at build time and is preserved. The native-dq output is LOAD-BEARING here — this
    # carrier emitted single-quoted 'STRIPPED' for a no-$() value, so a has-$() value now switches to a
    # DOUBLE-quoted output to keep the preserved span executing; the TPs below prove detection is
    # IDENTICAL to base (True==base).
    @pytest.mark.parametrize("label,cmd", [
        ("curl -d $()+danger",   'curl -d "as of $(date): note %s" %s' % (BD, _FC1_URL)),
        ("curl --data $()+danger", 'curl --data "$(date) drop %s" %s' % (BD, _FC1_URL)),
    ])
    @requires_prefix_fc1
    def test_closure(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True, "%s: must be over-blocked at pre-fix (else vacuous): %r" % (label, cmd)
        assert D(cmd) is False, "%s: F-C1 must CLOSE the curl -d coarse-substitution over-block: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("curl -d $(mal)",   'curl -d "$(%s)" %s' % (BD, _FC1_URL)),
        ("curl -d embedded", 'curl -d "pre $(%s) post" %s' % (BD, _FC1_URL)),
        ("curl -d + tail",   'curl -d "ok $(date)" %s && %s' % (_FC1_URL, BD)),
    ])
    @requires_prefix_fc1
    def test_truepositive(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True and D(cmd) is True, \
            "%s: real substitution in a curl -d value must stay caught at BOTH (native-dq): %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("benign sub",   'curl -d "user=$(whoami)" %s' % _FC1_URL),
        ("no sub",       'curl -d "just a note" %s' % _FC1_URL),
        ("sq untouched", "curl -d '$(date) %s' %s" % (BD, _FC1_URL)),
    ])
    def test_control(self, label, cmd):
        assert D(cmd) is False, "%s: benign/sq curl -d must stay False: %r" % (label, cmd)


class TestFC1GhApiSelectorCarrier:
    # C9: the -q/--jq value is a jq filter / Go template evaluated by gh against the API response (not a
    # local shell command); only the $() span executes at build time and is preserved. Native-dq output
    # is load-bearing exactly as C8 — the TPs prove True==base.
    @pytest.mark.parametrize("label,cmd", [
        ("gh -q $()+danger",   'gh api repos/o/r -q "as of $(date): %s"' % BD),
        ("gh --jq $()+danger", 'gh api repos/o/r --jq "$(date) %s"' % BD),
    ])
    @requires_prefix_fc1
    def test_closure(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True, "%s: must be over-blocked at pre-fix (else vacuous): %r" % (label, cmd)
        assert D(cmd) is False, "%s: F-C1 must CLOSE the gh-api selector coarse-substitution over-block: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("gh -q $(mal)",   'gh api repos/o/r -q "$(%s)"' % BD),
        ("gh --jq $(mal)", 'gh api repos/o/r --jq "$(%s)"' % BD),
        ("gh -q + tail",   'gh api repos/o/r -q "ok $(date)" && %s' % BD),
    ])
    @requires_prefix_fc1
    def test_truepositive(self, label, cmd):
        assert D_PREFIX_FC1(cmd) is True and D(cmd) is True, \
            "%s: real substitution in a gh-api selector must stay caught at BOTH (native-dq): %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("jq filter",    'gh api repos/o/r -q ".name"'),
        ("benign sub",   'gh api repos/o/r -q "$(date)"'),
        ("sq untouched", "gh api repos/o/r -q '$(date) %s'" % BD),
    ])
    def test_control(self, label, cmd):
        assert D(cmd) is False, "%s: benign/sq gh-api selector must stay False: %r" % (label, cmd)


class TestEchoPrintfMultiArgClosure:
    # REALIGNED (cycle 5b) — was TestFC1MultiArgBoundary in cycle 4, where the echo/printf multi-arg
    # over-block (carrier-3 anchor matched ONLY the FIRST dq arg, so a 2nd dq arg carrying danger was
    # never stripped) was pinned as a PRE-EXISTING out-of-scope anchor gap (True==both), and the pin
    # docstring foretold "a later cycle may fold this anchor-coverage class." Cycle 5b IS that fold:
    # the carve-out (e2145b44) strips EVERY positional echo/printf arg, so these are now CLOSURES.
    # Non-vacuity flips against the carve-out's OWN sharp immediate parent (D_PREFIX_ECHO = e2145b44^ =
    # a542e21b, where echo/printf still stripped only the first arg) per the per-fix-discriminator
    # discipline. The no-substitution form flips too (the carve-out strips args regardless of $()).
    @requires_prefix_echo
    def test_printf_multiarg_has_sub_closed(self):
        cmd = 'printf "%%s\\n" "note $(date) %s"' % BD
        assert D_PREFIX_ECHO(cmd) is True, "must be over-blocked at pre-carve a542e21b (else vacuous): %r" % cmd
        assert D(cmd) is False, "the carve-out must CLOSE the printf multi-arg over-block: %r" % cmd

    @requires_prefix_echo
    def test_printf_multiarg_no_sub_closed(self):
        # The no-substitution form ALSO flips True->False: the carve-out strips every positional arg
        # regardless of $(), so the 2nd-arg danger literal is now stripped.
        cmd = 'printf "%%s\\n" "note %s"' % BD
        assert D_PREFIX_ECHO(cmd) is True, "must be over-blocked at pre-carve a542e21b (else vacuous): %r" % cmd
        assert D(cmd) is False, "the carve-out must CLOSE the printf multi-arg no-substitution over-block: %r" % cmd


class TestFC1StripSurfaceMechanism:
    # Document the native-dq span-scope surface (literal -> STRIPPED, span preserved VERBATIM) per
    # carrier, so a mechanism regression is visible.
    def test_echo_span_scoped(self):
        assert STRIP('echo "as of $(date): note %s"' % BD) == 'echo "STRIPPED$(date)STRIPPED"'

    def test_echo_truepositive_span_preserved(self):
        assert STRIP('echo "$(%s)"' % BD) == 'echo "$(%s)"' % BD

    def test_herestring_span_scoped(self):
        assert STRIP('cat <<< "as of $(date): note %s"' % BD) == 'cat <<<"STRIPPED$(date)STRIPPED"'

    def test_curl_data_native_dq_span_scoped(self):
        # C8: the has-$() output is DOUBLE-quoted (native dq), NOT the single-quoted 'STRIPPED' the
        # no-$() path emits — this keeps the preserved span executing (the under-block-safety hinge).
        assert STRIP('curl -d "as of $(date): note %s" %s' % (BD, _FC1_URL)) \
            == 'curl -d "STRIPPED$(date)STRIPPED" %s' % _FC1_URL

    def test_curl_data_truepositive_span_preserved(self):
        assert STRIP('curl -d "$(%s)" %s' % (BD, _FC1_URL)) == 'curl -d "$(%s)" %s' % (BD, _FC1_URL)

    def test_gh_selector_native_dq_span_scoped(self):
        assert STRIP('gh api repos/o/r -q "as of $(date): %s"' % BD) \
            == 'gh api repos/o/r -q "STRIPPED$(date)STRIPPED"'


class TestFC1ReDoSLinearity:
    # The reused _preserve_substitution_spans scanner is O(n) across all 4 carriers; a light linearity
    # pin (measured ~46 ms at n=16000).
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_carrier_scanner_linear(self, n):
        assert _elapsed_ms('echo "' + "$(" * n + '"') < 1000.0, "echo unterminated $( n=%d" % n
        assert _elapsed_ms('curl -d "$(' + "(" * n + ")" * n + ')" ' + _FC1_URL) < 1000.0, "curl -d nested parens n=%d" % n


# ===========================================================================
# ===  #1176 ECHO/PRINTF MULTI-ARG CARVE-OUT — remediation cycle 5b. The     ===
# ===  carrier-3 first-arg-only strip is replaced by a span+per-arg loop that ===
# ===  strips EVERY positional echo/printf arg (bounded to echo/printf, which ===
# ===  never execute their args). NON-VACUITY vs D_PREFIX_ECHO (a542e21b =    ===
# ===  e2145b44^): every 2nd+ arg over-block flips True(pre-carve)->False;     ===
# ===  under-block guards (piped/procsub/tail/inline-malicious) hold True;     ===
# ===  the still-open C5/C7/C8 positional class stays pinned True==both        ===
# ===  (deferred audit). EMPIRICALLY ground-truthed at a542e21b vs HEAD.       ===
# ===========================================================================

class TestEchoPrintfCarveoutClosure:
    # Every quoted positional arg is now stripped, so danger in a 2nd/3rd arg (dq or sq, any order,
    # with or without a substitution/backtick) closes. NOTE: a SINGLE-arg has-$()/backtick case is NOT
    # here — F-C1 already closed those (False==False vs this baseline); the carve-out closes the
    # MULTI-arg surface. Non-vacuity flips against D_PREFIX_ECHO.
    @pytest.mark.parametrize("label,cmd", [
        ("echo 2nd dq arg + sub",  'echo "first arg" "note $(date) %s"' % BD),
        ("echo 2nd dq arg no-sub", 'echo "first arg" "note %s"' % BD),
        ("echo 3rd dq arg",        'echo "a" "b" "drop %s"' % BD),
        ("echo 2nd sq arg",        "echo 'a' 'note %s'" % BD),
        ("echo mixed dq,sq",       'echo "a" \'note %s\'' % BD),
        ("echo mixed sq,dq",       'echo \'a\' "note %s"' % BD),
        ("echo 2nd-arg backtick",  'echo "first" "ran `hostname`, then %s"' % BD),
        ("printf fmt+data + sub",  'printf "%%s" "note $(date) %s"' % BD),
        ("printf fmt+data no-sub", 'printf "%%s" "note %s"' % BD),
    ])
    @requires_prefix_echo
    def test_multiarg_closed(self, label, cmd):
        assert D_PREFIX_ECHO(cmd) is True, \
            "%s: must be over-blocked at pre-carve a542e21b (else vacuous): %r" % (label, cmd)
        assert D(cmd) is False, "%s: the carve-out must CLOSE the echo/printf multi-arg over-block: %r" % (label, cmd)


class TestEchoPrintfCarveoutUnderBlock:
    # The carve-out must NOT open an under-block. Output-routing to a shell (| bash / >(bash)) is the
    # only execution path and is SKIPPED by the outer guard (whole command preserved -> caught). An
    # executing tail after an unquoted separator stays OUTSIDE the span (leg-locality). A real
    # $(malicious) in ANY arg is span-preserved and stays caught. All True at BOTH baselines.
    @pytest.mark.parametrize("label,cmd", [
        ("dq piped to shell",   'echo "%s" | bash' % BD),
        ("sq piped to shell",   "echo '%s' | bash" % BD),
        ("2nd-arg piped",       'echo "a" "%s" | bash' % BD),
        ("process-sub",         'echo "%s" > >(bash)' % BD),
        ("executing && tail",   'echo "x" && %s' % BD),
        ("executing ; tail",    'echo "x" ; %s' % BD),
        ("inline $() 1st arg",  'echo "$(%s)"' % BD),
        ("inline $() 2nd arg",  'echo "a" "$(%s)"' % BD),
    ])
    @requires_prefix_echo
    def test_underblock_stays_caught(self, label, cmd):
        assert D_PREFIX_ECHO(cmd) is True and D(cmd) is True, \
            "%s: the carve-out must NOT open an under-block (True at both): %r" % (label, cmd)


class TestEchoPrintfCarveoutControls:
    # Benign echo/printf and cross-context mentions stay False (no danger to over-block).
    @pytest.mark.parametrize("label,cmd", [
        ("benign echo",        'echo "hello world"'),
        ("benign printf",      'printf "%%s\\n" "just a note"'),
        ("benign sub",         'echo "release $(date +%F)"'),
        ("cross-context git-msg", 'git commit -m "run echo to test the thing"'),
    ])
    def test_benign_stays_false(self, label, cmd):
        assert D(cmd) is False, "%s: benign/cross-context must stay False: %r" % (label, cmd)


class TestEchoPrintfDeferredBoundary:
    # DEFERRED-BOUNDARY pins — the STILL-OPEN positional over-block class the carve-out did NOT touch
    # (it is echo/printf-only by construction). One REPRESENTATIVE PER CARRIER (C5/C7/C8), deliberately
    # NOT an exhaustive enumeration: the cert's job is to RECORD the boundary (echo/printf multi-arg
    # CLOSED vs the structural positional class DEFERRED), not to pin the deferred class — that is the
    # dedicated anchor-coverage audit's job, and over-pinning would couple this cert to a class it does
    # not fix. Each carrier's non-message positional/flag value is NOT stripped, so a danger literal
    # there over-blocks — True==both, structurally untouched by the carve-out. Every example uses a
    # PLAIN danger literal (NO substitution) — that IS the no-substitution proof: the over-block does
    # not depend on a $(), it is the structural positional gap, so a future reader does not mistake
    # True==both for a carve-out miss. When the deferred audit lands, these flip to closures.
    @pytest.mark.parametrize("label,cmd", [
        ("C5 git --trailer",  'git commit --trailer "Ref: %s"' % BD),
        ("C7 gh --assignee",  'gh issue create --title "ok" --assignee "%s"' % BD),
        ("C8 curl -H header", 'curl -H "X-Note: %s" %s' % (BD, _FC1_URL)),
    ])
    @requires_prefix_echo
    def test_deferred_positional_stays_true_both(self, label, cmd):
        assert D_PREFIX_ECHO(cmd) is True and D(cmd) is True, \
            "%s: STILL-DEFERRED positional over-block must be True==both (untouched by echo/printf carve-out): %r" % (label, cmd)


class TestEchoPrintfCarveoutStripSurface:
    # Document the span+per-arg-loop surface: EVERY quoted arg -> STRIPPED (dq span-scoped, sq
    # bareword); the verb stays intact; leg-locality keeps a tail outside; a $() span is preserved.
    def test_all_args_stripped(self):
        assert STRIP('echo "first arg" "note %s"' % BD) == "echo STRIPPED STRIPPED"

    def test_mixed_quote_args_stripped(self):
        assert STRIP('echo "a" \'note %s\'' % BD) == "echo STRIPPED STRIPPED"

    def test_printf_fmt_and_data_stripped(self):
        assert STRIP('printf "%%s\\n" "note $(date) %s"' % BD) == 'printf STRIPPED "STRIPPED$(date)STRIPPED"'

    def test_leg_locality_tail_preserved(self):
        assert STRIP('echo "x" && %s' % BD) == "echo STRIPPED && %s" % BD

    def test_piped_whole_preserved(self):
        # The outer piped-to-shell skip preserves the whole command (danger stays visible -> caught).
        assert STRIP('echo "%s" | bash' % BD) == 'echo "%s" | bash' % BD

    def test_inline_second_arg_substitution_preserved(self):
        assert STRIP('echo "a" "$(%s)"' % BD) == 'echo STRIPPED "$(%s)"' % BD


class TestEchoPrintfCarveoutReDoS:
    # The span + per-arg loop is O(total length); a many-args input and a nested-paren input both stay
    # linear (measured ~142 ms at 16000 args, ~50 ms nested).
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_carveout_linear(self, n):
        assert _elapsed_ms('echo ' + '"a" ' * n + '"%s"' % BD) < 2000.0, "echo many-args n=%d" % n
        assert _elapsed_ms('echo "$(' + "(" * n + ")" * n + ')"') < 2000.0, "echo nested parens n=%d" % n
