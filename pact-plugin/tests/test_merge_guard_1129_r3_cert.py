"""
Location: pact-plugin/tests/test_merge_guard_1129_r3_cert.py
Summary: COMPREHENSIVE BIDIRECTIONAL certification for #1129 R3 — scoping the two
         routing flags (_has_pipe_to_shell, _has_process_substitution_to_shell) to
         the EXECUTED SURFACE so a routing token inside quoted carrier DATA / a
         heredoc BODY no longer disables the carrier strips and re-exposes co-residing
         destructive text — PLUS the R3-fix remediation of the arm-B anchor under-block
         the R3 view first introduced (found by the independent security pass #18).

         Proven against the REAL classifier, up to FOUR baked columns, NEVER a byte-diff
         (#1118):
           - BASE   = 51e6c5a5 (pre-R3): the flags compute over the raw command.
           - R3HEAD = 72bacaf8 (R3 shipped, carries the arm-B regression): the flags
             compute over the pure space-mask view. For the OUTPUT-SIDE procsub rows
             this column IS the 'anchor-restore reverted' state, so R-1..R-4 R3HEAD=False
             is the permanent non-vacuity discriminator for the arm-B anchor restore.
           - FIX1   = b313ecaa (R3-fix1: arm-B anchor restored for a SPACE separator ONLY):
             the 'fix2 reverted' state. Non-space bash blanks (tab / blank-run-ending-in-tab)
             are UNDER-BLOCKED here, so FIX1=False on those rows is the permanent fix2
             non-vacuity discriminator.
           - PATCH  = live 7561d32d (R3 + fix1 anchor restore + fix2 whitespace widening):
             piped stays on the space-mask view; procsub reads _procsub_anchor_view whose
             anchor walk-left now skips any UNMASKED BASH BLANK (space/tab), not just space.

         Separator classification (by SHELL EXECUTION SEMANTICS, not by arm-B \\s+ match):
           a quoted-writer procsub `echo "<d>" | <writer><SEP>>(bash)` executes a fanout
           ONLY when <SEP> is a run of bash BLANKS (space/tab). fix2 catches ALL such blanks
           (FIX-blanks, must-be-True). Non-blank whitespace {NL,CR,FF,VT} is non-executing
           (\\n = command separator; \\r/\\f/\\v glue the writer into a nonexistent command),
           and zero-separator adjacency is a pre-existing arm-B \\s+ gap — both DIFFERENTIAL-
           False, asserted PATCH=False (NOT must-be-True; demanding True is the trap toward
           the over-broad \\s fix that re-introduces over-blocks).

         Row classes (design §8 matrix + §13.8 remediation):
           - FIX (carrier-DATA over-block CLOSURE): base True -> R3HEAD False -> PATCH
             False. Pipe/procsub/input-procsub token inside a quoted carrier value or a
             heredoc body across carriers 1/3/5/7/7b/7c/7d/8/9. The base=True column
             proves each was a genuine faithful-click over-block (never a vacuous green).
           - REGRESSION-FIX (§13.8 R-1..R-4): base True -> R3HEAD False -> PATCH True.
             A genuinely-EXECUTING output-side procsub whose FIFO-writer name is quoted
             (`echo "<d>" | "tee" >(bash)`, class "tee"/'tee'/"dd"/"cat"). base catches
             (the closing `"` anchors arm B); R3's space-mask blanks the anchor ->
             UNDER-BLOCK (the R3HEAD=False discriminator == the confirmed auth regression);
             the R3-fix restores the anchor -> PATCH re-catches. The R3HEAD=False column
             is the load-bearing proof that the anchor-restore is non-vacuous.
           - PRESERVE (executing routing stays caught): base True -> PATCH True — real
             `| sh`/`| bash`/`| xargs bash`, $()/backtick/eval/bash -c, heredoc opener
             tails, input-side `bash <(..)`, honest `tee >(bash)`/arm-A `> >(bash)`, and
             carrier value + EXECUTING tail (C18/C19). Plus §13.8 P-1 (B3 survives:
             quoted `>(bash)` DATA stays False at PATCH) and P-2 (the incidental
             `>("ba"sh)` under-block R3's mask closed must NOT regress).
           - DIFFERENTIAL (pre-existing under-blocks / correct negatives): False==False
             ==False. #1146 `| sudo sh`, quoted shell name `| "sh"`, `| sudo sh` in body,
             content-gated (token, no danger), non-shell `<(` in body, #1133 bare heredoc,
             stderr `2> >(bash)` exclusion; PLUS the separator DIFFERENTIALs above
             (non-executing {NL,CR,FF,VT} + zero-sep adjacency). Asserted False (NOT
             must-stay-True) — asserting True on a pre-existing under-block or a
             non-executing form would wrongly implicate R3/fix (plan cert caveat).
           - BONUS-CLOSURE (document, don't gate): `echo "<d>" | "tee" >("ba"sh)`
             base False -> PATCH True — genuinely-executing under-block the R3-fix closes;
             MORE correct than base, not an over-block.

NON-VACUITY (permanent, in-suite): the base(51e6c5a5), R3HEAD(72bacaf8) AND FIX1(b313ecaa)
classifiers are loaded via `git show` + exec and asserted IN-TEST, so the cert can never be
vacuously green — a FIX row's base=True proves the vector existed; a REGRESSION-FIX row's
R3HEAD=False proves the arm-B under-block was real and the anchor-restore is load-bearing; a
FIX-blank row's FIX1=False (on non-space blanks) proves fix2's whitespace widening is
independently load-bearing; a future regression flips a PATCH column and reds the row. Three
real-source-revert legs (1: relocation, 2: heredoc-excision, 3: the fix2 one-line-predicate
revert `view[k]==excised[k]` -> `view[k]==" "`, which flips the non-space-blank rows back to
False while SPACE stays True) are documented in the HANDOFF with cardinality — they exercise
the LIVE 7561d32d source.

Cross-refs: docs/architecture/merge-guard-r3-carrier-data-pipe-scope.md §6 (invariants),
§8 (R3 cert plan), §13 (R3-fix remediation), §13.8 (updated cert plan). Destructive verbs
are assembled at runtime (PF/BD/M9/SH) so this file carries no raw force-push/force-delete
/merge literal and stays inert to the live guard; probe forms are never run as shell.
"""
import subprocess
import sys
import types
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.merge_guard_common as mgc  # noqa: E402

# --- Baked classifiers loaded from git ONCE for permanent in-test non-vacuity.
#     BASE = pre-R3 (flags over raw command); R3HEAD = R3 shipped (space-mask view,
#     carries the arm-B under-block == 'security-fix reverted' for output-side procsub);
#     FIX1 = R3-fix1 (arm-B anchor restored for a SPACE separator ONLY == 'fix2-reverted'),
#     the discriminator for the whitespace-widening fix2 (non-space bash blanks).
_BASE_SHA = "51e6c5a5"   # pre-R3
_R3_SHA = "72bacaf8"     # R3 shipped (arm-B regression present)
_FIX1_SHA = "b313ecaa"   # R3-fix1: space-only anchor restore (fix2-reverted state)


def _load_classifier(sha):
    """Load merge_guard_common as it existed at `sha`, or None if unavailable
    (git missing, or a SHALLOW clone lacking the commit) so collection SUCCEEDS and the
    base/R3HEAD non-vacuity rows self-SKIP (@requires_history) instead of aborting the
    file. Mirrors test_merge_guard_1129_r2_cert._load_classifier."""
    wt = Path(__file__).resolve().parents[2]  # worktree root (tests/../../)
    try:
        src = subprocess.check_output(
            ["git", "-C", str(wt), "show",
             sha + ":pact-plugin/hooks/shared/merge_guard_common.py"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    mod = types.ModuleType("merge_guard_common_1129r3_" + sha)
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"  # so its `from shared.x import ...` resolve on sys.path
    try:
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    except Exception:
        return None
    return mod


_BASE = _load_classifier(_BASE_SHA)
_R3 = _load_classifier(_R3_SHA)
_FIX1 = _load_classifier(_FIX1_SHA)
# None-safe: a bare `_BASE.is_dangerous_command` would AttributeError at import when the
# baked source is unavailable (shallow clone), re-aborting collection. D_BASE/D_R3/D_FIX1
# are only ever called by the @requires_history-guarded columns.
D_BASE = _BASE.is_dangerous_command if _BASE is not None else None
D_R3 = _R3.is_dangerous_command if _R3 is not None else None
D_FIX1 = _FIX1.is_dangerous_command if _FIX1 is not None else None
D = mgc.is_dangerous_command

requires_history = pytest.mark.skipif(
    _BASE is None or _R3 is None or _FIX1 is None,
    reason="base/R3HEAD/FIX1 non-vacuity requires merged history (shallow clone / missing history)",
)

# Destructive literals assembled at runtime — this file carries no raw literal.
PF = "git " + "push " + "--force origin main"    # force-push
BD = "git " + "branch " + "-D main"              # force branch-delete
M9 = "gh " + "pr " + "merge 9"                    # merge
SH = "s" + "h"                                    # shell name, kept un-obvious


# ===========================================================================
# §13.8 REGRESSION-FIX — quoted-writer output-side procsub: the arm-B anchor
# under-block R3 introduced and the R3-fix re-catches.
#   base True  (closing quote anchors arm B on the raw command)
#   R3HEAD False (space-mask blanks the anchor == the CONFIRMED auth under-block
#                 == 'security-fix reverted' — the leg-3 non-vacuity discriminator)
#   PATCH True (surgical LEFT-anchor restoration re-catches)
# ===========================================================================
class TestR3FixQuotedWriterRegression:

    @pytest.mark.parametrize("label,cmd", [
        ("R-1 dq writer \"tee\"", 'echo "%s" | "tee" >(bash)' % PF),
        ("R-2 sq writer 'tee'", "echo \"%s\" | 'tee' >(bash)" % PF),
        ("R-3 dq writer \"dd\"", 'echo "%s" | "dd" >(bash)' % PF),
        ("R-4 dq writer \"cat\"", 'echo "%s" | "cat" >(bash)' % PF),
    ])
    @requires_history
    def test_quoted_writer_regression_recaught(self, label, cmd):
        assert D_BASE(cmd) is True, \
            "%s: base must CATCH the executing quoted-writer procsub (else vacuous): %r" % (label, cmd)
        assert D_R3(cmd) is False, \
            "%s: R3HEAD MUST under-block (the confirmed arm-B regression == security-fix reverted; " \
            "else the anchor-restore is vacuous): %r" % (label, cmd)
        assert D(cmd) is True, \
            "%s: PATCH must RE-CATCH via the restored left anchor: %r" % (label, cmd)


# ===========================================================================
# §13-fix2 SEPARATOR CLASS — whitespace-widening of the arm-B anchor restore.
# The quoted-writer procsub `echo "<d>" | <writer><SEP>>(bash)` genuinely EXECUTES a
# fanout ONLY when <SEP> is a run of bash BLANKS (space/tab): those are shell word
# separators that fan echo's stdout into the >(bash) FIFO. fix2 (7561d32d) widened the
# anchor-restore walk-left from 'literal space' to 'any unmasked bash blank'. Rows are
# classified by SHELL EXECUTION SEMANTICS, not by whether arm-B's \s+ regex matches.
# FIX1(b313ecaa) restored the anchor for a SPACE separator ONLY, so it is the fix2
# non-vacuity discriminator: non-space blanks were under-blocked there, re-caught here.
# ===========================================================================
_WRITERS = [("dq-tee", '"tee"'), ("sq-tee", "'tee'"), ("dq-dd", '"dd"'), ("dq-cat", '"cat"')]
_FIX_BLANKS = [("SPACE", " "), ("2xSPACE", "  "), ("TAB", "\t"),
               ("TAB+TAB", "\t\t"), ("SP+TAB", " \t"), ("TAB+SP", "\t ")]
_NONSPACE_BLANKS = [("TAB", "\t"), ("TAB+TAB", "\t\t"), ("SP+TAB", " \t"), ("TAB+SP", "\t ")]
_NONEXEC_WS = [("NL", "\n"), ("CR", "\r"), ("FF", "\f"), ("VT", "\v")]


def _procsub(writer, sep):
    return 'echo "%s" | %s%s>(bash)' % (PF, writer, sep)


class TestR3Fix2BlankSeparatorFix:

    @pytest.mark.parametrize("wl,w", _WRITERS)
    @pytest.mark.parametrize("sl,s", _FIX_BLANKS)
    def test_blank_separated_fanout_caught_at_patch(self, wl, w, sl, s):
        # Genuinely-executing: a bash-blank run separates the quoted writer from >(bash),
        # so echo's stdout fans into the shell FIFO. MUST be caught at the widened PATCH.
        assert D(_procsub(w, s)) is True, \
            "%s writer / %s sep: blank-separated procsub fanout must be caught: %r" % (wl, sl, _procsub(w, s))

    @pytest.mark.parametrize("wl,w", _WRITERS)
    @pytest.mark.parametrize("sl,s", _FIX_BLANKS)
    @requires_history
    def test_blank_separated_was_a_genuine_base_vector(self, wl, w, sl, s):
        assert D_BASE(_procsub(w, s)) is True, \
            "%s / %s: base must catch the executing fanout (else vacuous): %r" % (wl, sl, _procsub(w, s))

    @pytest.mark.parametrize("wl,w", _WRITERS)
    @pytest.mark.parametrize("sl,s", _NONSPACE_BLANKS)
    @requires_history
    def test_nonspace_blank_underblocked_at_fix1_recaught_at_patch(self, wl, w, sl, s):
        # THE fix2 non-vacuity discriminator: a non-space bash blank (tab / a multi-blank run
        # whose char adjacent to >( is a tab) was UNDER-BLOCKED at FIX1 (space-only anchor
        # restore) and is RE-CAUGHT at the widened PATCH. Bakes fix2's load-bearingness.
        cmd = _procsub(w, s)
        assert D_FIX1(cmd) is False, \
            "%s / %s: FIX1 (space-only) MUST under-block this non-space blank (else fix2 vacuous): %r" % (wl, sl, cmd)
        assert D(cmd) is True, \
            "%s / %s: widened PATCH must RE-CATCH the non-space blank fanout: %r" % (wl, sl, cmd)


class TestR3Fix2NonExecutingDifferential:
    """Non-blank whitespace {NL,CR,FF,VT} between the quoted writer and >(bash) is NOT an
    executing fanout: \\n is a command separator (two commands, no fanout); \\r/\\f/\\v glue to
    the writer (`tee\\r` = nonexistent command). base's arm-B \\s+ OVER-matches them
    (regex-match != shell-execution), but the widen fix targets executing bash blanks ONLY and
    deliberately does NOT replicate the over-match. Asserted PATCH=False, NOT must-be-True:
    demanding must-be-True is the trap that pushes toward the over-broad \\s fix which
    re-introduces over-blocks. (\\n verdict: architect §13-fix-2.5 — DIFFERENTIAL-False.)"""

    @pytest.mark.parametrize("wl,w", _WRITERS)
    @pytest.mark.parametrize("sl,s", _NONEXEC_WS)
    def test_nonexecuting_whitespace_false_at_patch(self, wl, w, sl, s):
        assert D(_procsub(w, s)) is False, \
            "%s / %s: non-executing whitespace must NOT be caught (fix targets executing blanks only): %r" % (wl, sl, _procsub(w, s))

    @pytest.mark.parametrize("wl,w", _WRITERS)
    @pytest.mark.parametrize("sl,s", _NONEXEC_WS)
    @requires_history
    def test_nonexecuting_whitespace_is_a_base_overmatch(self, wl, w, sl, s):
        # Documents WHY these are differential: base=True is an arm-B \s+ OVER-match on a
        # non-executing form (regex matched; the shell would not fan out).
        assert D_BASE(_procsub(w, s)) is True, \
            "%s / %s: base arm-B \\s+ over-match expected (documents the differential): %r" % (wl, sl, _procsub(w, s))


class TestR3Fix2ZeroSepPreExistingGap:
    """Zero-separator adjacency `<writer>>(bash)` is a PRE-EXISTING arm-B gap: arm B requires
    \\s+ (>=1 whitespace), so adjacency never matches even with the anchor present — base never
    caught it either (base=False). NOT an R3 regression, out of R3/fix scope. Asserted
    PATCH=False (differential); base=False proves it is pre-existing, not a closure the fix owes."""

    @pytest.mark.parametrize("wl,w", _WRITERS)
    def test_zero_sep_false_at_patch(self, wl, w):
        assert D(_procsub(w, "")) is False, \
            "%s: zero-sep adjacency is a pre-existing arm-B gap, must stay False: %r" % (wl, _procsub(w, ""))

    @pytest.mark.parametrize("wl,w", _WRITERS)
    @requires_history
    def test_zero_sep_never_caught_by_base(self, wl, w):
        assert D_BASE(_procsub(w, "")) is False, \
            "%s: base never caught zero-sep (pre-existing gap, NOT an R3 regression): %r" % (wl, _procsub(w, ""))


# ===========================================================================
# §13.8 PRESERVE — the R3-fix must not regress B3 (data-resident procsub stays
# closed) or the incidental >("ba"sh) win R3's mask closed.
# ===========================================================================
class TestR3FixPreserves:

    def test_b3_data_resident_procsub_stays_closed_at_patch(self):
        # P-1: quoted `>(bash)` is DATA — fully masked -> no surviving `>(` in the anchor
        # view -> arm B has nothing to anchor -> stays False. The R3 over-block fix SURVIVES
        # the remediation. (base over-blocked it; R3HEAD closed it; PATCH keeps it closed.)
        cmd = 'gh issue create --body "%s >(bash)"' % PF
        assert D(cmd) is False, "P-1: R3-fix must NOT re-over-block B3 data-resident procsub: %r" % cmd

    @requires_history
    def test_b3_was_a_genuine_base_over_block(self):
        cmd = 'gh issue create --body "%s >(bash)"' % PF
        assert D_BASE(cmd) is True, "P-1: base must over-block B3 (else the survival row is vacuous)"
        assert D_R3(cmd) is False, "P-1: R3 closed the B3 over-block"

    def test_incidental_double_quoted_shell_name_win_preserved(self):
        # P-2: `>("ba"sh)` — writer `tee` UNQUOTED, shell name quoted. R3's space-mask
        # turned `>("ba"sh)` into `>(    sh)` and arm B's `>\\(\\s*` spans to `sh` -> a base
        # under-block R3 INCIDENTALLY closed. The R3-fix leaves everything right of `>(`
        # as the mask, so this stays caught. Must NOT regress.
        cmd = 'echo "%s" | tee >("ba"sh)' % PF
        assert D(cmd) is True, "P-2: incidental >(\"ba\"sh) win must NOT regress: %r" % cmd

    @requires_history
    def test_incidental_win_was_a_base_under_block(self):
        cmd = 'echo "%s" | tee >("ba"sh)' % PF
        assert D_BASE(cmd) is False, "P-2: base under-blocked (else 'incidental win' is vacuous)"
        assert D_R3(cmd) is True, "P-2: R3's space-mask incidentally closed it"


# ===========================================================================
# §13.8 BONUS-CLOSURE (document, don't gate) — quoted writer AND quoted shell name.
# base False -> PATCH True. Genuinely-executing under-block the R3-fix additionally
# closes (more correct than base); NOT an over-block.
# ===========================================================================
class TestR3FixBonusClosure:

    @requires_history
    def test_quoted_writer_and_quoted_shell_bonus_closed(self):
        cmd = 'echo "%s" | "tee" >("ba"sh)' % PF
        assert D_BASE(cmd) is False, "bonus: base under-blocked this executing form"
        assert D(cmd) is True, \
            "bonus: PATCH closes it (writer quote anchors arm B; shell quotes mask to spaces): %r" % cmd


# ===========================================================================
# §13.8 DIFFERENTIAL — stderr exclusion + no-space arm-B gap (F==F==F).
# ===========================================================================
class TestR3FixDifferential:

    @pytest.mark.parametrize("label,cmd", [
        ("D-1a stderr 2> benign", 'echo hi 2> >(bash)'),
        ("D-1b stderr 2> danger", 'echo "%s" 2> >(bash)' % PF),
        ("D-2a #1146 | \"sh\"", 'echo "%s" | "%s"' % (PF, SH)),
        # (zero-separator no-whitespace arm-B gap is covered comprehensively across the
        #  writer-class by TestR3Fix2ZeroSepPreExistingGap.)
    ])
    def test_differential_stays_false_at_patch(self, label, cmd):
        # Pre-existing under-blocks / correct negatives. The R3-fix must NOT convert a
        # stderr-excluded form into an arm-B match. NOT must-stay-True.
        assert D(cmd) is False, "%s: R3-fix must not widen this pre-existing negative: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("D-1a stderr 2> benign", 'echo hi 2> >(bash)'),
        ("D-2a #1146 | \"sh\"", 'echo "%s" | "%s"' % (PF, SH)),
    ])
    @requires_history
    def test_differential_unchanged_across_all_three(self, label, cmd):
        assert D_BASE(cmd) is False and D_R3(cmd) is False and D(cmd) is False, \
            "%s: must be False==False==False (differential, not implicating R3): %r" % (label, cmd)


# ===========================================================================
# §13.8 PRESERVE — honest output-side procsub stays caught (base True -> PATCH True).
# ===========================================================================
class TestHonestProcsubPreserved:

    @pytest.mark.parametrize("label,cmd", [
        ("H-1 | tee >(bash)", 'echo "%s" | tee >(bash)' % PF),
        ("H-2 arm-A > >(bash)", 'echo "%s" > >(bash)' % PF),
        ("H-3 tee \"out.log\" >(bash)", 'echo "%s" | tee "out.log" >(bash)' % PF),
        ("H-4 input-side bash <(..)", 'bash <(echo "%s")' % PF),
    ])
    def test_honest_procsub_true_at_patch(self, label, cmd):
        assert D(cmd) is True, "%s: honest executing procsub must stay caught at PATCH: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("H-1 | tee >(bash)", 'echo "%s" | tee >(bash)' % PF),
        ("H-2 arm-A > >(bash)", 'echo "%s" > >(bash)' % PF),
        ("H-4 input-side bash <(..)", 'bash <(echo "%s")' % PF),
    ])
    @requires_history
    def test_honest_procsub_true_on_base(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: honest procsub must be a genuine base catch: %r" % (label, cmd)


# ===========================================================================
# §8 BOUNDARY + DOCUMENTED RESIDUAL — fail-closed direction and the accepted
# over-block residuals (comment-resident token §3.5; ANSI-C \\' desync §11 / plan-D6).
# All stay True across base/R3HEAD/PATCH — R3 neither introduces nor removes them.
# The residual rows PIN the over-block so a future change cannot silently flip them;
# they are asserted True==True==True as DOCUMENTED residuals (over-block-safe), NOT as
# R3 wins.
# ===========================================================================
class TestR3BoundaryAndResidual:

    @pytest.mark.parametrize("label,cmd", [
        # probe-D4: an unbalanced quote near a routing token — the mask fails TOWARD
        # unmasked, so the token stays visible and the flag fires (fail-closed). A
        # malformed-quote click is not a faithful click, so this residual over-block is
        # accepted (design §5 pt5).
        ("probe-D4 unbalanced quote", 'echo "%s | %s' % (PF, SH)),
        # probe-D5: a routing token inside a carrier value AND a REAL destructive tail on
        # a second leg — the tail is executed surface and stays caught.
        ("probe-D5 body token + && tail", 'gh pr edit 123 --body "x | %s" && %s' % (SH, PF)),
        ("probe-D5 body token + ; tail", 'gh pr edit 123 --body "x | %s" ; %s' % (SH, PF)),
        # E3: comment-resident routing token — comments are deliberately NOT excised from
        # the view (§3.5: excising them on the masked view is an under-block trap), so this
        # stays a documented residual over-block. Narrower than the surface R3 fixes.
        ("E3 comment-resident token", 'echo "%s" # docs: | %s' % (PF, SH)),
        # E4: an ANSI-C \\' desyncs the single-quote mask so the token stays visible — the
        # exact mirror of plan-D6's ratified ANSI-C residual (design §11).
        ("E4 ANSI-C backslash-quote desync", "gh pr comment 1 --body $'%s\\' | %s'" % (PF, SH)),
    ])
    def test_boundary_residual_true_at_patch(self, label, cmd):
        assert D(cmd) is True, \
            "%s: fail-closed / documented-residual over-block must stay caught at PATCH: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("probe-D4 unbalanced quote", 'echo "%s | %s' % (PF, SH)),
        ("E3 comment-resident token", 'echo "%s" # docs: | %s' % (PF, SH)),
        ("E4 ANSI-C backslash-quote desync", "gh pr comment 1 --body $'%s\\' | %s'" % (PF, SH)),
    ])
    @requires_history
    def test_boundary_residual_unchanged_across_all_three(self, label, cmd):
        assert D_BASE(cmd) is True and D_R3(cmd) is True and D(cmd) is True, \
            "%s: R3 must neither introduce nor remove this residual (True==True==True): %r" % (label, cmd)


# ===========================================================================
# §8 PRESERVE — executing routing stays caught (base True -> R3HEAD True -> PATCH True).
# ===========================================================================
class TestR3PreserveExecuting:

    @pytest.mark.parametrize("label,cmd", [
        ("C1 exec | sh", 'echo "%s" | %s' % (PF, SH)),
        ("C2 exec | bash", 'echo "%s" | bash' % PF),
        ("C11 exec | xargs bash", 'echo "%s" | xargs bash' % PF),
        ("C12 second-leg && | sh", 'echo ok && echo "%s" | %s' % (PF, SH)),
        ("C6 $()-in-body (plan-D4)", 'gh pr create --body "$(%s)"' % PF),
        ("C7 backtick-in-body", 'gh pr create --body "`%s`"' % PF),
        ("C8 eval", 'eval "%s"' % PF),
        ("C9 bash -c", 'bash -c "%s"' % PF),
        ("C15 heredoc | bash opener tail", 'bash <<EOF | bash\n%s\nEOF' % PF),
        ("C16 heredoc > >(bash) opener tail", 'cat <<EOF > >(bash)\n%s\nEOF' % PF),
        ("C17 shell-fed heredoc body | sh", 'bash <<EOF\n%s | %s\nEOF' % (PF, SH)),
        ("C18 carrier value + | bash tail", 'gh pr create --title "%s" | bash' % PF),
        ("C19 carrier value + > >(bash) tail", 'gh pr create --title "%s" > >(bash)' % PF),
    ])
    def test_executing_routing_true_at_patch(self, label, cmd):
        assert D(cmd) is True, "%s: executing routing must stay caught at PATCH: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("C1 exec | sh", 'echo "%s" | %s' % (PF, SH)),
        ("C2 exec | bash", 'echo "%s" | bash' % PF),
        ("C15 heredoc | bash opener tail", 'bash <<EOF | bash\n%s\nEOF' % PF),
        ("C16 heredoc > >(bash) opener tail", 'cat <<EOF > >(bash)\n%s\nEOF' % PF),
        ("C17 shell-fed heredoc body | sh", 'bash <<EOF\n%s | %s\nEOF' % (PF, SH)),
        ("C18 carrier value + | bash tail", 'gh pr create --title "%s" | bash' % PF),
    ])
    @requires_history
    def test_executing_routing_true_on_base_and_r3(self, label, cmd):
        assert D_BASE(cmd) is True and D_R3(cmd) is True, \
            "%s: executing routing must be caught on base AND R3HEAD (R3 narrows no detection): %r" % (label, cmd)


# ===========================================================================
# §8 FIX — carrier-DATA over-block CLOSURE (base True -> R3HEAD False -> PATCH False).
# A pipe/procsub/input-procsub routing token inside a quoted carrier value or a heredoc
# body no longer disables the carrier strip. base=True proves each was a genuine vector.
# ===========================================================================
class TestR3FixCarrierDataOverBlock:

    @pytest.mark.parametrize("label,cmd", [
        ("A1 gh pr edit --body dq", 'gh pr edit 123 --body "%s | %s"' % (PF, SH)),
        ("A2 gh pr edit --title dq", 'gh pr edit 123 --title "%s | %s"' % (PF, SH)),
        ("A7 release --notes dq", 'gh release create v1 --notes "%s | %s"' % (PF, SH)),
        ("A8 gist --desc dq", 'gh gist create f.txt --desc "%s | %s"' % (PF, SH)),
        ("A9 git tag -m dq", 'git tag -m "%s | %s" v1' % (PF, SH)),
        ("A10 pr comment --body sq", "gh pr comment 1 --body '%s | %s'" % (PF, SH)),
        ("A17 naked heredoc body | sh", 'cat <<EOF\n%s | %s\nEOF' % (PF, SH)),
        ("A-c8 curl -d dq", 'curl -d "%s | %s" https://x.example' % (PF, SH)),
        ("A-c9 gh api --jq sq", "gh api repos/o/r --jq '%s | %s'" % (PF, SH)),
        ("B3 output procsub-in-body", 'gh pr edit 123 --body "%s > >(bash)"' % PF),
        ("B1fix input-procsub-in-body", 'gh pr edit 123 --body "bash <(echo %s)"' % PF),
        ("probeD1 cross-value", 'gh pr edit 123 --title "%s" --body "x | %s"' % (PF, SH)),
        ("probeD2 cross-leg newline", 'gh pr edit 1 --body "x | %s"\ngh pr edit 2 --body "%s"' % (SH, PF)),
        ("E6 ANSI-C $'..| sh' body", "gh pr comment 1 --body $'%s | %s'" % (PF, SH)),
        ("E7 concat \"..\"'| sh' body", "gh pr comment 1 --body \"%s\"'| %s'" % (PF, SH)),
    ])
    def test_carrier_data_over_block_closed_at_patch(self, label, cmd):
        assert D(cmd) is False, "%s: carrier-DATA over-block must stay CLOSED at PATCH: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("A1 gh pr edit --body dq", 'gh pr edit 123 --body "%s | %s"' % (PF, SH)),
        ("A9 git tag -m dq", 'git tag -m "%s | %s" v1' % (PF, SH)),
        ("A17 naked heredoc body | sh", 'cat <<EOF\n%s | %s\nEOF' % (PF, SH)),
        ("A-c8 curl -d dq", 'curl -d "%s | %s" https://x.example' % (PF, SH)),
        ("B3 output procsub-in-body", 'gh pr edit 123 --body "%s > >(bash)"' % PF),
        ("B1fix input-procsub-in-body", 'gh pr edit 123 --body "bash <(echo %s)"' % PF),
        ("probeD2 cross-leg newline", 'gh pr edit 1 --body "x | %s"\ngh pr edit 2 --body "%s"' % (SH, PF)),
        ("E6 ANSI-C $'..| sh' body", "gh pr comment 1 --body $'%s | %s'" % (PF, SH)),
        ("E7 concat \"..\"'| sh' body", "gh pr comment 1 --body \"%s\"'| %s'" % (PF, SH)),
    ])
    @requires_history
    def test_over_block_was_a_genuine_base_vector(self, label, cmd):
        assert D_BASE(cmd) is True, \
            "%s: base must OVER-BLOCK (else the closure row is vacuous): %r" % (label, cmd)
        assert D_R3(cmd) is False, "%s: R3 closed the carrier-DATA over-block: %r" % (label, cmd)


# ===========================================================================
# §8 DIFFERENTIAL — pre-existing under-blocks / correct negatives (F==F==F).
# Asserted False==False (NOT must-stay-True): asserting True on a pre-existing
# under-block would wrongly implicate R3 (plan cert caveat).
# ===========================================================================
class TestR3Differential:

    @pytest.mark.parametrize("label,cmd", [
        ("E1 #1146 | sudo sh", 'echo "%s" | sudo %s' % (PF, SH)),
        ("E2 quoted shell name | \"sh\"", 'echo "%s" | "%s"' % (PF, SH)),
        ("A6 | sudo sh in body", 'gh pr edit 123 --body "%s | sudo %s"' % (PF, SH)),
        ("C10 content-gated (token,no danger)", 'gh pr edit 123 --body "just text | %s"' % SH),
        ("B1diff non-shell <( in body", 'gh pr edit 123 --body "diff <(echo %s) f"' % PF),
        ("E5 #1133 bare heredoc body", 'cat <<EOF\n%s\nEOF' % PF),
    ])
    def test_differential_false_at_patch(self, label, cmd):
        assert D(cmd) is False, "%s: pre-existing negative must stay False at PATCH: %r" % (label, cmd)

    @pytest.mark.parametrize("label,cmd", [
        ("E1 #1146 | sudo sh", 'echo "%s" | sudo %s' % (PF, SH)),
        ("E2 quoted shell name | \"sh\"", 'echo "%s" | "%s"' % (PF, SH)),
        ("A6 | sudo sh in body", 'gh pr edit 123 --body "%s | sudo %s"' % (PF, SH)),
    ])
    @requires_history
    def test_differential_false_across_all_three(self, label, cmd):
        assert D_BASE(cmd) is False and D_R3(cmd) is False and D(cmd) is False, \
            "%s: differential must be False==False==False (not implicating R3): %r" % (label, cmd)
