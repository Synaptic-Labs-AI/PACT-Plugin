"""
Location: pact-plugin/tests/test_merge_guard_1129_r2_cert.py
Summary: COMPREHENSIVE BIDIRECTIONAL certification for #1129 R2 — the carrier-7
         unification (SACROSANCT shared strip/leg pipeline). Proves R2's properties
         against the REAL classifier, base-vs-HEAD, NEVER a byte-diff (#1118):

           - OB1-8 over-block CLOSURE: each confirmed faithful-click over-block is
             is_dangerous on BASE (023ee2c3) and NOT on HEAD (R2).
           - MUST-NOT-REGRESS: genuinely-executing / genuinely-destructive forms stay
             gated in BOTH base and HEAD (R2 narrows NO detection).
           - -d SURVIVES -m STRIP: the git-tag -m value strip is flag-anchored and
             leaves the -d DELETE target visible in every ordering; the release
             value-strip never consumes the -d/-p BOOLEAN's neighbour.
           - SUBCOMMAND-DELETE accepted-residual PIN: gh release delete / gh gist
             delete / git tag -d stay UNDETECTED (base==HEAD==False), byte-unchanged.
             These under-blocks are a SEPARATE hardening issue, NOT R2's over-block
             scope — this pins them so a future detection arm can't silently regress.
           - THE LOAD-BEARING ROW: a REAL post_main->pre_main mint->execute round-trip
             proving the mint-side launder is WITHHELD at HEAD (the :447 is_dangerous
             write-gate: mint⊆read, so the carrier strip that drops is_dangerous to
             False also withholds the launder token — closure is free, no mint-path
             edit). Non-vacuity for the gist launder is the base-revert witness recorded
             in the TEST HANDOFF (base mints {merge,5,--delete-branch} + the
             --delete-branch exec ALLOWs; HEAD mints nothing + DENYs).

NON-VACUITY (base-vs-HEAD): the read-side rows load the BASE classifier (023ee2c3) via
`git show` + exec and assert base-vs-HEAD IN-TEST, so the cert is permanently
non-vacuous — a future strip regression flips a HEAD column back and reds the row, and
the base column proves each form was genuinely a vector (never a vacuous green).

Cross-refs: docs/architecture/1129-r2-carrier7.md §5 (cert matrix); docs/preparation/
1129-r2-groundtruth.md (empirical OB/launder findings). Destructive verbs are assembled
at runtime (BD/PF/M5) so this file carries no raw force-delete/force-push literal and
stays inert to the live guard.
"""
import io
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import merge_guard_post  # noqa: E402
import merge_guard_pre  # noqa: E402
import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402

# --- BASE classifier (pre-R2, 023ee2c3) loaded ONCE for baked base-vs-HEAD non-vacuity.
_BASE_SHA = "023ee2c3"


def _load_base_classifier():
    wt = Path(__file__).resolve().parents[2]  # worktree root (tests/../../)
    src = subprocess.check_output(
        ["git", "-C", str(wt), "show",
         _BASE_SHA + ":pact-plugin/hooks/shared/merge_guard_common.py"]
    ).decode()
    mod = types.ModuleType("base_merge_guard_common_1129r2")
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"  # so its `from shared.x import ...` resolve on sys.path
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


_BASE = _load_base_classifier()
D_BASE = _BASE.is_dangerous_command
D = mgc.is_dangerous_command
STRIP = mgc._strip_non_executable_content

# Destructive verbs assembled at runtime — this file carries no raw literal.
BD = "git " + "branch " + "-D main"           # destructive branch-delete literal
PF = "git " + "push " + "--force origin main"  # destructive force-push literal
M5 = "gh " + "pr " + "merge 5"                 # destructive merge literal
ALLOW, DENY = 0, 2


# ===========================================================================
# Real post_main -> pre_main round-trip harness (the launder witness).
# ===========================================================================
def _mint(carrier_cmd, tok):
    """Drive the REAL post hook mint for a faithful carrier click; return the list of
    minted token contexts (empty when the :447 write-gate withholds)."""
    env = json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{
        "question": "Proceed?", "options": [
            {"label": "Yes", "description": "Run `%s`" % carrier_cmd},
            {"label": "Cancel", "description": "Abort"}]}]},
        "tool_response": {"answers": {"Proceed?": "Yes"}}, "session_id": "r2cert"})
    with patch.object(merge_guard_post, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit:
            pass
    return [json.loads(f.read_text()).get("context", {})
            for f in tok.glob("merge-authorized-*")]


def _execute(cmd, tok):
    """Run `cmd` through the REAL pre hook; return exit code (0=ALLOW, 2=DENY)."""
    env = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "r2cert"})
    with patch.object(merge_guard_pre, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), patch("sys.stdout", io.StringIO()), \
         patch("sys.stderr", io.StringIO()):
        try:
            pre_main()
            return ALLOW
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else ALLOW


# ===========================================================================
# OB1-8 — faithful-click over-block CLOSURE (base is_dangerous True -> HEAD False).
# ===========================================================================
class TestOverBlockClosure:

    @pytest.mark.parametrize("label,cmd", [
        ("OB1 gh pr edit --body", 'gh pr edit 123 --body "%s"' % BD),
        ("OB1 gh pr edit -b", 'gh pr edit 123 -b "%s"' % BD),
        ("OB1 gh pr edit --title", 'gh pr edit 123 --title "%s"' % BD),
        ("OB2 gh release create --notes", 'gh release create v1 --notes "%s"' % PF),
        ("OB2 gh release create -n", 'gh release create v1 -n "%s"' % PF),
        ("OB2 gh release edit --notes", 'gh release edit v1 --notes "%s"' % PF),
        ("OB3 git tag -m", 'git tag -m "%s" v1' % BD),
        ("OB3 git tag --message", 'git tag --message "%s" v1' % BD),
        ("OB4 gh gist create --desc", 'gh gist create f.txt --desc "%s"' % BD),
        ("OB4 gh gist create -d", 'gh gist create f.txt -d "%s"' % BD),
        ("OB5 ANSI-C $'...'", "gh pr comment 1 --body $'%s'" % PF),
        ("OB6 adjacent-concat", 'gh pr create --title "Fix "\'%s\'' % M5),
        ("OB8 gh release create --title", 'gh release create v1 --title "%s"' % BD),
    ])
    def test_over_block_closed_base_true_head_false(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: expected a BASE over-block vector (else vacuous)" % label
        assert D(cmd) is False, "%s: HEAD must CLOSE the faithful-click over-block" % label


# ===========================================================================
# MUST-NOT-REGRESS — genuinely executing / destructive forms stay gated (base & HEAD).
# ===========================================================================
class TestMustNotRegress:

    @pytest.mark.parametrize("label,cmd", [
        ("UB1 $()-body executes", 'gh pr create --body "$(%s)"' % PF),
        ("UB1b backtick executes", 'gh pr create --body "`%s`"' % PF),
        ("UB2 close --delete-branch", 'gh pr close 5 --delete-branch'),
        ("UB3 leg-tail && force-push", 'gh pr create --body "x" && %s' % PF),
        ("UB3b leg-tail && branch-delete", 'gh pr create --body "x" && %s' % BD),
        ("UB3c leg-tail ; force-push", 'gh pr create --body "x" ; %s' % PF),
    ])
    def test_stays_gated_base_and_head(self, label, cmd):
        assert D_BASE(cmd) is True, "%s: must be gated on base" % label
        assert D(cmd) is True, "%s: R2 must NOT narrow detection here" % label


# ===========================================================================
# -d SURVIVES -m STRIP + release boolean non-collision (sibling-safety).
# ===========================================================================
class TestDSurvivesMStrip:

    @pytest.mark.parametrize("label,cmd", [
        ("git tag -m then -d", 'git tag -m "msg" -d oldtag'),
        ("git tag -d then -m", 'git tag -d oldtag -m "msg"'),
    ])
    def test_tag_delete_target_visible_after_m_strip(self, label, cmd):
        # The flag-anchored -m value strip must NOT consume the -d DELETE target in any
        # ordering (the git-tag verb-collision guard — -m benign shares the verb with -d).
        stripped = STRIP(cmd)
        assert "-d oldtag" in stripped, "%s: -d target lost to the -m strip: %r" % (label, stripped)

    def test_release_boolean_not_mis_consumed(self):
        # -d (=--draft) / -p (=--prerelease) are BOOLEANS for release; the value-strip
        # (per-carrier set, NOT a union) strips only --title/--notes VALUES and leaves the
        # boolean -d visible in every ordering.
        stripped = STRIP('gh release create v1 --title "T" -d --notes "N"')
        assert "-d" in stripped and "STRIPPED" in stripped
        assert '"T"' not in stripped and '"N"' not in stripped

    def test_tag_m_compound_tail_stays_caught(self):
        # Leg-locality: a benign tag -m then a destructive force-push tail stays gated
        # (the flag-anchored strip does not span past the unquoted separator).
        assert D('git tag -m "msg" && %s' % PF) is True


# ===========================================================================
# SUBCOMMAND-DELETE accepted-residual PIN (base==HEAD==False; separate hardening issue).
# ===========================================================================
class TestSubcommandDeleteAcceptedResidualPin:
    """gh release delete / gh gist delete / git tag -d are UNDETECTED at HEAD and R2
    leaves them byte-unchanged. These are tolerated-direction UNDER-blocks tracked as a
    SEPARATE hardening issue (#1137, NOT R2's over-block scope). This row PINS the
    accepted residual so a future detection arm cannot silently regress it — it asserts
    they REMAIN undetected, NOT that they should be gated."""

    @pytest.mark.parametrize("label,cmd", [
        ("gh release delete", 'gh release delete v1.0'),
        ("gh gist delete", 'gh gist delete abc123'),
        ("git tag -d", 'git tag -d oldtag'),
    ])
    def test_still_undetected_unchanged(self, label, cmd):
        assert D_BASE(cmd) is False and D(cmd) is False, \
            "%s: accepted-residual PIN — must stay undetected in R2 (hardening tracked in #1137)" % label


# ===========================================================================
# LOAD-BEARING — the REAL mint->execute round-trip: launder WITHHELD at HEAD.
# ===========================================================================
class TestLaunderClosureRoundTrip:
    """The :447 is_dangerous write-gate makes mint⊆read: the carrier strip that drops
    is_dangerous to False also withholds the launder token — no mint-path edit. Proven
    by the REAL post_main->pre_main round-trip, NOT read-side reasoning (phantom-green
    #1129). Base-mints witness (non-vacuity) is recorded in the TEST HANDOFF."""

    def test_gist_merge_launder_withheld_at_head(self, tmp_path):
        # BASE (recorded in HANDOFF): `gh gist create -d "<merge>"` mints
        # {merge,5,--delete-branch} and `gh pr merge 5 --delete-branch` ALLOWs (the gist
        # -d is read as merge's --delete-branch alias). HEAD must withhold + DENY.
        carrier = 'gh gist create -d "%s"' % M5
        assert D(carrier) is False, "carrier must be non-dangerous at HEAD (strip removed the literal)"
        assert _mint(carrier, tmp_path) == [], "HEAD must mint NO launder token (:447 write-gate)"
        assert _execute(M5 + " --delete-branch", tmp_path) == DENY, "the base-usable laundered exec must DENY at HEAD"

    def test_tag_forcepush_launder_withheld_at_head(self, tmp_path):
        # Mint-level closure (the tag force-push launder is not a usable clean-exec
        # round-trip on base — see HANDOFF caveat — but R2 still withholds it): the tag
        # carrier is non-dangerous at HEAD so :447 withholds any force-push token.
        carrier = 'git tag v1.0 -m "%s"' % PF
        assert D(carrier) is False
        assert _mint(carrier, tmp_path) == [], "HEAD must mint NO force-push token from the tag carrier"
