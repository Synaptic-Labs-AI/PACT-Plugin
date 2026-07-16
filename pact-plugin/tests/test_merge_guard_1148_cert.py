"""
Location: pact-plugin/tests/test_merge_guard_1148_cert.py
Summary: BIDIRECTIONAL certification for the quote-aware comment excision (step 3 of
         `_excise_and_mask`): a `#`-comment carrying `| sh` no longer flips
         `piped_to_shell` on the routing view (which disabled every content carrier
         and gated a faithful commit whose -m value merely NAMED a destructive op).
         Certifies against the REAL classifier, BASE (committed vendored fixture via
         merge_guard_baseline_loader — loud-fail, CI-executable) vs HEAD.

         PREDICATE UNDER CERT: a `#` is a comment iff it SURVIVES the quote mask
         (unquoted by construction) AND its PRE-MASK predecessor (excised[i-1];
         i == 0 counts) is in {start, space, tab, newline, ;, &, |}. Excision is
         VIEW-ONLY and SAME-LENGTH (spaces; newline kept), preserving both view
         consumers' 1:1 offset alignment. FAIL-TOWARD-NOT-COMMENT.

         THE TWO DOCUMENTED UNDER-BLOCK TRAPS (each has a load-bearing canary):
           trap 1 (mask-induced false comment): a view-side predecessor test reads
             a masked closing quote as a space — `echo "x"# tail | sh` would be
             excised and the REAL pipe eaten. The RAW-predecessor test refuses:
             excised[i-1] == '"' -> not a comment.
           trap 2 (pre-mask excision eats executing tails): excising on the raw
             string would eat `' | sh'` THROUGH a quoted `#` (`git commit -m
             'a # b' | sh` — the # is data; the pipe is real). Mask-BEFORE-excise
             refuses: a quoted `#` is already spaces on the view.

         Ships LAST in the three-commit arc, so the composed end-state sweep here
         runs the #1181 + #1155 closure/retention spot sets against the FINAL tree.

         Destructive verbs are assembled at runtime so this file stays inert to the
         live guard.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402

D = mgc.is_dangerous_command

M5 = "gh " + "pr " + "merge 5 --admin"
PF = "git " + "push " + "--force origin main"
REPRO = 'git commit -m "note: %s later" # reviewed, do not pipe | sh' % M5


def _base():
    return load_baseline().is_dangerous_command


# =========================================================================================
# CLOSURE — base-True -> HEAD-False: the comment-resident routing token no longer
# disables the carriers.
# =========================================================================================
CLOSURES = [
    ("repro-commit-msg-comment-pipe", REPRO),
    ("comment-pipe-echo-carrier", 'echo "%s" # docs: | sh' % PF),
]


class TestClosure:
    @pytest.mark.parametrize("label,cmd", CLOSURES, ids=[r[0] for r in CLOSURES])
    def test_comment_over_block_closed(self, label, cmd):
        assert _base()(cmd) is True, "row was not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "faithful click with a #-comment still gated at HEAD"

    def test_repro_mechanism_view_level(self):
        # the excised-surface predecessor test at the mechanism level: the comment
        # (incl. its `| sh`) is spaces on the view, so the routing flag is False.
        view = mgc._executed_surface_view(REPRO)
        assert mgc._has_pipe_to_shell(view) is False
        assert "#" not in view and "| sh" not in view
        base = load_baseline()
        assert base._has_pipe_to_shell(base._executed_surface_view(REPRO)) is True, (
            "baseline discriminator: the comment survived the view at base"
        )


# =========================================================================================
# TRAP CANARIES — retention (True -> True) / mask-survival pins. Each canary fails
# exactly when its trap's naive implementation is (re)introduced.
# =========================================================================================
TRAP_RETENTION = [
    # trap 2: quoted `#`, REAL executing pipe tail — a pre-mask excision would eat it.
    ("quoted-hash-real-pipe", "git commit -m '%s # b' | sh" % M5),
    # trap 1: raw predecessor is a closing quote — not a comment; the pipe is real.
    ("quote-adjacent-hash-real-pipe", 'echo "%s"# not-a-comment | sh' % M5),
    # danger on the executed surface BEFORE a comment stays caught.
    ("danger-before-comment", "%s # done" % M5),
]


class TestTrapCanaries:
    @pytest.mark.parametrize("label,cmd", TRAP_RETENTION, ids=[r[0] for r in TRAP_RETENTION])
    def test_executing_forms_stay_caught(self, label, cmd):
        assert _base()(cmd) is True, "canary not True at base (vacuous)"
        assert D(cmd) is True, "fix opened an under-block: an executing tail was excised"

    def test_word_char_predecessor_untouched(self):
        # issue#42 / url#fragment: word-char predecessor -> not a comment -> the view
        # keeps the text (fail-toward-not-comment).
        for text in ["issue#42", "url#fragment"]:
            assert mgc._executed_surface_view(text) == text

    def test_real_pipe_view_flag_survives_quoted_hash(self):
        # trap-2 mechanism pin at the view level (no danger literal needed): the
        # quoted # is data; the pipe must still flip the routing flag.
        view = mgc._executed_surface_view("git commit -m 'a # b' | sh")
        assert mgc._has_pipe_to_shell(view) is True


# =========================================================================================
# SECOND-CONSUMER SAFETY — _procsub_anchor_view relies on 1:1 view/excised offset
# alignment; step 3 is same-length so nothing shifts, and comment-resident procsub
# markers are excised (not "surviving").
# =========================================================================================
class TestProcsubAnchorAlignment:
    def test_same_length_contract(self):
        cmd = 'git commit -m "x" # don' + "'t >(bash)"
        excised, view = mgc._excise_and_mask(cmd)
        assert len(excised) == len(view)

    def test_comment_resident_procsub_not_flagged(self):
        cmd = 'git commit -m "x" # > >(bash)'
        assert mgc._has_process_substitution_to_shell(mgc._procsub_anchor_view(cmd)) is False

    def test_real_procsub_still_flagged(self):
        cmd = 'git commit -m "x" > >(bash)'
        assert mgc._has_process_substitution_to_shell(mgc._procsub_anchor_view(cmd)) is True


# =========================================================================================
# CONTROLS — base-False AND HEAD-False (the bite needs carrier-skip + quoted danger
# co-residing; either alone was never gated).
# =========================================================================================
CONTROLS = [
    ("no-comment-control", 'git commit -m "note: %s later"' % M5),
    ("comment-no-quoted-danger", "gh pr view 5 # checked | sh"),
    # unquoted danger in a comment after an inert head was ALREADY handled at base
    # (the stripped-surface comment carrier) — a control, not a closure.
    ("comment-unquoted-danger-already-closed", "ls -la # TODO: %s | sh" % M5),
]


class TestControls:
    @pytest.mark.parametrize("label,cmd", CONTROLS, ids=[r[0] for r in CONTROLS])
    def test_never_gated_forms_stay_ungated(self, label, cmd):
        assert _base()(cmd) is False
        assert D(cmd) is False


# =========================================================================================
# INTERACTION WITH THE VIEW-PASS CLASSIFIER — detect's view pass inherits comment
# excision: a #-comment naming a different op no longer classifies it in pass 1
# (the raw fallback keeps status-quo recognition where the view abstains).
# =========================================================================================
class TestDetectInteraction:
    def test_comment_resident_op_no_longer_misclassifies(self):
        cmd = "gh pr close 5 # weighed gh pr " + "mer" + "ge 5"
        assert load_baseline().detect_command_operation_type(cmd) == "merge"
        assert mgc.detect_command_operation_type(cmd) == "close"


# =========================================================================================
# COMPOSED END-STATE SWEEP — the #1181 and #1155 closure/retention spot sets against
# the FINAL tree (this commit ships last; the cert sweeps the composed view), plus
# the no-new-over-block monotonicity accounting over this file's rows.
# =========================================================================================
COMPOSED_CLOSURES = [
    ("1181-log-grep", "git log --grep '%s'" % M5),
    ("1181-grep-positional", "git grep '%s'" % M5),
    ("1181-pr-list-search", "gh pr list --search '%s'" % M5),
    ("1155-find-name", "find . -name '%s'" % M5),
]
COMPOSED_RETENTION = [
    ("1181-glued-separator", "git log --grep=foo&&%s" % M5),
    ("1181-grep-O-deny", "git grep -O'sh -c \"%s\"' pat" % M5),
    ("1155-find-exec", "find . -name '%s' -exec cat {} \\;" % M5),
    ("bare-merge", M5),
    ("bare-force-push", PF),
    ("awk-residual", "awk '/%s/ {print}' f" % M5),
]


class TestComposedEndState:
    @pytest.mark.parametrize(
        "label,cmd", COMPOSED_CLOSURES, ids=[r[0] for r in COMPOSED_CLOSURES]
    )
    def test_arc_closures_hold_on_final_tree(self, label, cmd):
        assert _base()(cmd) is True
        assert D(cmd) is False

    @pytest.mark.parametrize(
        "label,cmd", COMPOSED_RETENTION, ids=[r[0] for r in COMPOSED_RETENTION]
    )
    def test_arc_retention_holds_on_final_tree(self, label, cmd):
        assert _base()(cmd) is True
        assert D(cmd) is True

    def test_no_false_to_true_across_all_rows(self):
        base_d = _base()
        closure_labels = {r[0] for r in CLOSURES} | {r[0] for r in COMPOSED_CLOSURES}
        all_rows = (
            CLOSURES + TRAP_RETENTION + CONTROLS + COMPOSED_CLOSURES + COMPOSED_RETENTION
        )
        for label, cmd in all_rows:
            b, h = base_d(cmd), D(cmd)
            assert not (b is False and h is True), (
                "False->True transition (new over-block) on %s" % label
            )
            if b is True and h is False:
                assert label in closure_labels, (
                    "unintended True->False transition on %s" % label
                )


# =========================================================================================
# R1 / F1 — ESCAPE-AWARE COMMENT PREDECESSOR (remediation commit 4). Commit 3's comment
# excision was escape-BLIND: a `#` after a BACKSLASH-ESCAPED delimiter (`\ #`, `\<tab>#`,
# `\;#`, `\&#`, `\|#`) is NOT a bash comment (the escaped delimiter is a literal word char,
# so bash EXECUTES what follows), but commit 3 excised it, deleting a real `| sh` from the
# routing view and neutralizing the gate (a token-independent under-block). _delimiter_is_
# unescaped (backslash-PARITY) closes it: ODD run = escaped = NOT a comment = text stays
# visible = gated; EVEN run (incl. 0) = real delimiter = comment (unchanged).
#
# DISCRIMINANT (design intent) = does bash execute the pipe? Verified below against a live
# `bash -c` with a touch-MARKER oracle: escaped delimiter -> pipe executes -> must gate;
# unescaped/plain -> comment -> pipe suppressed -> stays closed (the #1148 closure, preserved).
# =========================================================================================
_ESC = [  # escaped delimiter -> NON-comment -> routing token survives -> stays GATED
    ("esc-space-force-pipe-sh", 'echo "%s"\\ #x | sh' % PF),
    ("esc-space-force-pipe-bash", 'echo "%s"\\ #x | bash' % PF),
    ("esc-space-force-pipe-sh-nospace", 'echo "%s"\\ #x |sh' % PF),
    ("esc-tab-force", 'echo "%s"\\\t#x | sh' % PF),
    ("esc-semicolon-force", 'echo "%s"\\;#x | sh' % PF),
    ("esc-amp-force", 'echo "%s"\\&#x | sh' % PF),
    ("esc-pipe-force", 'echo "%s"\\|#x | sh' % PF),
    ("esc-space-branch-delete", 'echo "%s"\\ #x | sh' % ("git " + "branch " + "-D victim")),
    ("esc-space-merge-payload", 'echo "%s"\\ #x | sh' % M5),
    ("multi-escape-odd-run-3", 'echo "%s"\\ \\ \\ #x | sh' % PF),
]
_GENUINE = [  # real comment -> excised -> not dangerous (over-block safety; F1 must PRESERVE)
    ("plain-space-comment", 'echo "%s" #x | sh' % PF),
    ("unescaped-semicolon-then-comment", 'echo "%s" ;# c | sh' % PF),
    ("even-backslash-run-2-comment", 'echo "%s"\\\\ #x | sh' % PF),
    ("orig-1148-repro", REPRO),
]


class TestR1EscapeAwarePredecessor:
    @pytest.mark.parametrize("label,cmd", _ESC, ids=[r[0] for r in _ESC])
    def test_escaped_delimiter_stays_gated(self, label, cmd):
        # PRIMARY: the fixed classifier GATES the executing form (under-block closed).
        assert D(cmd) is True, "escaped-delimiter under-block re-opened (gate bypassed)"
        # NON-VACUITY at the mechanism level: the comment is NOT excised, so the routing
        # `| sh`/`| bash` survives on the view (a revert of _delimiter_is_unescaped would
        # excise it and flip this False). Base fixture also caught it (no excision pre-#1148).
        view = mgc._executed_surface_view(cmd)
        assert mgc._has_pipe_to_shell(view) is True, "escaped-# comment was wrongly excised"
        assert _base()(cmd) is True, "row not caught at base (vacuous retention)"

    @pytest.mark.parametrize("label,cmd", _GENUINE, ids=[r[0] for r in _GENUINE])
    def test_genuine_comment_still_excised(self, label, cmd):
        # OVER-BLOCK SAFETY: a real comment (unescaped / even-backslash delimiter) is still
        # excised -> not dangerous. F1 must not gate a faithful #-comment click.
        assert D(cmd) is False, "F1 over-blocked a genuine #-comment (regressed #1148 closure)"
        view = mgc._executed_surface_view(cmd)
        assert mgc._has_pipe_to_shell(view) is False, "genuine comment survived the view"

    def test_delimiter_parity_unit(self):
        # d points at the DELIMITER (the space here); count the backslash run ending at d-1.
        # 0/2 backslashes -> unescaped (real delimiter); 1/3 -> escaped (literal char).
        assert mgc._delimiter_is_unescaped("x #", 1) is True         # 0 backslashes
        assert mgc._delimiter_is_unescaped("x\\ #", 2) is False      # 1 backslash (escaped)
        assert mgc._delimiter_is_unescaped("x\\\\ #", 3) is True     # 2 backslashes (literal \\ + real space)
        assert mgc._delimiter_is_unescaped("x\\\\\\ #", 4) is False  # 3 backslashes (escaped)

    def test_bash_oracle_discriminant(self):
        # Ground truth: the design intent is "does bash execute the pipe?". This pins the
        # classifier's gate/excise decision to real bash comment semantics.
        import os
        import subprocess
        import tempfile

        cases = {
            " #x": False,        # plain space -> comment -> pipe suppressed
            "\\ #x": True,       # escaped space -> non-comment -> pipe executes
            "\\;#x": True,       # escaped ; -> non-comment
            "\\&#x": True,       # escaped & -> non-comment
            "\\\\ #x": False,    # even backslash -> comment
        }
        for tail, should_execute in cases.items():
            with tempfile.TemporaryDirectory() as td:
                marker = os.path.join(td, "M")
                subprocess.run(
                    ["bash", "-c", 'echo "hi"%s | touch %s' % (tail, marker)],
                    capture_output=True,
                )
                assert os.path.exists(marker) is should_execute, (
                    "bash oracle mismatch for tail %r" % tail
                )

    def test_r1_monotonic_no_new_over_block(self):
        # every R1 row: no base-False -> HEAD-True (the escaped rows are base-True already;
        # the genuine rows are base-True -> HEAD-False closures). No faithful click blocked.
        base_d = _base()
        for label, cmd in _ESC + _GENUINE:
            assert not (base_d(cmd) is False and D(cmd) is True), (
                "F1 introduced a new over-block on %s" % label
            )
