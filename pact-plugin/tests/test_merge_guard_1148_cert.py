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
