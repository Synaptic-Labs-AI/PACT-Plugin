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
             proving the mint-side launder is WITHHELD at HEAD. MECHANISM (architect §1,
             F1-cycle refinement — primary/secondary, not a blanket ":447"): PRIMARY closure
             = read-side AUTO-ALLOW — post-R2 is_dangerous(carrier)=False, so
             check_merge_authorization returns None (ALLOW), NO AskUserQuestion approval is
             raised, and the mint path is NEVER invoked with the carrier command in the
             normal flow. SECONDARY backstop = the :447 is_dangerous write-gate (ACCURATE,
             base-vs-HEAD verified): IF the mint IS driven (an AskUserQuestion whose clicked
             option carries the carrier command — what THIS cert harness simulates), :447
             withholds because is_dangerous=False. The gist launder is a real USABLE base
             launder R2 closes END-TO-END (base mints {merge,5,--delete-branch}, exec
             `gh pr merge 5 --delete-branch` ALLOWs; HEAD mints nothing + DENYs). The git-tag
             launder base token IS CLASSIFIED (push-to-main, target 'main"' — CORRUPTED by
             the closing quote captured inside the -m value), NOT unclassified/vacuous, so
             R2's git-tag closure is mint-level defense-in-depth, not a usable-exec launder.
           - F1 LEG-MERGE REGRESSION GUARD (HALT #64, fix 3abfa3dc): the pre-fix
             whole-command carrier-7d strip let _strip_flag_values consume `STRIPPED<SEP>gh`
             across an UNQUOTED separator, EATING the head of a no-space gh-headed destructive
             tail (`git tag -m "x";gh pr merge 5 --delete-branch`) -> is_dangerous=False ->
             auth-bypass. The span-scope fix restores leg-locality. These rows load a THIRD
             baked classifier (PRE-FIX R2 6f404f2e) and assert base=True / pre-fix=False /
             fixed=True (pre-fix is the discriminator). Includes the over-anchoring witness
             (the non-gobbling git-tag anchor must not cross a separator into a LATER git tag)
             + SAFE controls (spaced separator, git-headed tail) that must NOT flip.
           - NEW-CARRIER AXIS COVERAGE (review MINOR-1): the strip axes ($()/backtick
             preserve, exotic-quote closure, leg-locality) exercised on ALL 3 new carriers
             (release/gist/git-tag), not just carrier-7.

NON-VACUITY (base-vs-HEAD, and pre-fix-vs-fixed for F1): the read-side rows load the BASE
classifier (023ee2c3) AND the PRE-FIX R2 classifier (6f404f2e) via `git show` + exec and
assert the discrimination IN-TEST, so the cert is permanently non-vacuous — a future strip
regression flips a HEAD column back and reds the row, and the base/pre-fix column proves
each form was genuinely a vector (never a vacuous green).

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

# --- Baked classifiers loaded from git ONCE for in-test base-vs-HEAD non-vacuity:
#     BASE (pre-R2) for the OB-closure rows; PRE-FIX R2 (the buggy whole-command
#     carrier-7d) for the F1 leg-merge regression guard. The F1 discriminator is
#     pre-fix=False -> fixed=True; BASE has no 7d carrier so it does NOT flip on F1.
_BASE_SHA = "023ee2c3"    # pre-R2
_PREFIX_SHA = "6f404f2e"  # R2 pre-F1-fix (whole-command 7d — the HALT #64 regression)


def _load_classifier(sha):
    wt = Path(__file__).resolve().parents[2]  # worktree root (tests/../../)
    src = subprocess.check_output(
        ["git", "-C", str(wt), "show",
         sha + ":pact-plugin/hooks/shared/merge_guard_common.py"]
    ).decode()
    mod = types.ModuleType("merge_guard_common_1129r2_" + sha)
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"  # so its `from shared.x import ...` resolve on sys.path
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


_BASE = _load_classifier(_BASE_SHA)
_PREFIX = _load_classifier(_PREFIX_SHA)
D_BASE = _BASE.is_dangerous_command
D_PREFIX = _PREFIX.is_dangerous_command
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
    """PRIMARY closure = read-side auto-allow (is_dangerous=False -> check_merge_authorization
    returns None -> no approval event -> the mint is NEVER invoked in the normal flow); the
    :447 is_dangerous write-gate is the ACCURATE SECONDARY backstop THIS harness drives
    directly (architect §1). Proven by the REAL post_main->pre_main round-trip, NOT read-side
    reasoning (phantom-green #1129). The gist base-mint witness is baked below (MINOR-2); the
    git-tag base token IS classified (push-to-main) but its target 'main\"' is CORRUPTED (not
    a usable launder), so R2's git-tag closure is mint-level defense-in-depth."""

    def test_gist_merge_launder_withheld_at_head(self, tmp_path):
        # `gh gist create -d "<merge>"` — the gist -d is read as merge's --delete-branch
        # alias. HEAD must withhold + DENY. (Base-mint proven in the sibling row below.)
        carrier = 'gh gist create -d "%s"' % M5
        assert D(carrier) is False, "carrier must be non-dangerous at HEAD (strip removed the literal)"
        assert _mint(carrier, tmp_path) == [], "HEAD must mint NO launder token"
        assert _execute(M5 + " --delete-branch", tmp_path) == DENY, "the base-usable laundered exec must DENY at HEAD"

    def test_gist_base_mint_witness_is_a_usable_launder(self):
        # MINOR-2 (baked base-mint non-vacuity): the BASE (023ee2c3) classifier both GATES
        # the gist carrier AND extracts a USABLE {merge, pr 5, --delete-branch} context from
        # it — i.e. a real usable launder EXISTED on base for HEAD to close (not a vacuous
        # withhold). HEAD classifies it non-dangerous (the -d value is stripped).
        carrier = 'gh gist create -d "%s"' % M5
        assert D_BASE(carrier) is True
        base_ctx = _BASE.extract_command_context(carrier)
        assert base_ctx.get("operation_type") == "merge"
        assert base_ctx.get("pr_number") == "5"
        assert "--delete-branch" in base_ctx.get("bound_flags", [])
        assert D(carrier) is False  # HEAD: closed

    def test_tag_forcepush_launder_withheld_at_head(self, tmp_path):
        # Mint-level closure. The git-tag base token IS classified (push-to-main) but its
        # target 'main"' is CORRUPTED (the closing quote captured inside the -m value), so it
        # was NEVER a usable clean-exec launder — R2's tag closure is defense-in-depth. The
        # tag carrier is non-dangerous at HEAD so no push-to-main token mints.
        carrier = 'git tag v1.0 -m "%s"' % PF
        assert D(carrier) is False
        assert _mint(carrier, tmp_path) == [], "HEAD must mint NO push-to-main token from the tag carrier"


# ===========================================================================
# F1 LEG-MERGE REGRESSION GUARD (HALT #64, fix 3abfa3dc) — base / PRE-FIX / fixed.
# Co-derived vector space with security-engineer #57. The PRE-FIX column (6f404f2e,
# buggy whole-command 7d) is the discriminator: it MUST be False (the auth-bypass), so
# these rows can never be vacuously green.
# ===========================================================================
class TestF1LegMergeRegression:

    _TAG = 'git tag -a v1 -m "Release v1.0"'
    # F1 is the EATEN-HEAD family {gh, curl, wget} (security-engineer #57 completeness pass):
    # the pre-fix whole-command 7d over-consumed `STRIPPED<SEP>` into the next leg and ate ANY
    # destructive op's HEAD token, not just gh's. curl/wget need a real destructive endpoint
    # (…/git/refs/…) or the head isn't destructive. Client verbs assembled at runtime (inert).
    _HEADS = [
        ("gh-pr-merge--delete-branch", "gh " + "pr merge 5 --delete-branch"),
        ("gh-pr-close--delete-branch", "gh " + "pr close 5 --delete-branch"),
        ("gh-api--X-DELETE", "gh " + "api -X DELETE repos/o/r/git/refs/heads/main"),
        ("curl--X-DELETE", "curl " + "-X DELETE https://api.github.com/repos/o/r/git/refs/heads/main"),
        ("wget--method-DELETE", "wget " + "--method=DELETE https://api.github.com/repos/o/r/git/refs/heads/main"),
    ]
    # Both message quotings are vulnerable pre-fix (arm3 re-touches the arm1 STRIPPED bareword).
    _MSGS = [("quoted", 'git tag -a v1 -m "Release v1.0"'), ("unquoted", 'git tag -m note')]

    @pytest.mark.parametrize("sep", [";", "&&", "|"])
    @pytest.mark.parametrize("head_label,head", _HEADS)
    @pytest.mark.parametrize("msg_label,tag", _MSGS)
    def test_nospace_destructive_head_leg_merge_closed(self, sep, head_label, head, msg_label, tag):
        # `git tag -m <msg><SEP-no-space><destructive-head>` — the F1 eaten-head auth-bypass,
        # across the {gh, curl, wget} head family × {;,&&,|} × {quoted, unquoted} message.
        cmd = tag + sep + head
        assert D_BASE(cmd) is True, "base must catch the tail (no 7d carrier): %r" % cmd
        assert D_PREFIX(cmd) is False, "PRE-FIX 7d MUST leg-merge (the F1 auth-bypass) else vacuous: %r" % cmd
        assert D(cmd) is True, "fixed span-scope must catch the tail (F1 closed): %r" % cmd

    @pytest.mark.parametrize("tag", ['git tag -m "a\'b"', "git tag -m 'a\"b'"])
    def test_mixed_quote_message_tail_still_caught(self, tag):
        # A quote-INSIDE the message: arm1/arm2 consume the quoted span atomically (no dangling
        # quote), so the no-space gh tail survives and is caught at fixed. Robustness guard
        # (security-engineer #57 noted it's not an under-block vector, but worth one pin).
        cmd = tag + ";" + M5 + " --delete-branch"
        assert D(cmd) is True, "mixed-quote message must not leg-merge the tail: %r" % cmd

    @pytest.mark.parametrize("sep", [";", "&&", "|"])
    def test_safe_control_spaced_separator_no_flip(self, sep):
        # A SPACE before gh keeps the head intact on the pre-fix strip -> no leg-merge.
        cmd = "%s %s %s --delete-branch" % (self._TAG, sep, M5)
        assert D_BASE(cmd) is True and D_PREFIX(cmd) is True and D(cmd) is True

    def test_safe_control_git_headed_tail_no_flip(self):
        # No-space but git-HEADED tail survives via the permissive _GIT_PREFIX (not eaten).
        cmd = self._TAG + ";" + PF
        assert D_BASE(cmd) is True and D_PREFIX(cmd) is True and D(cmd) is True

    @pytest.mark.parametrize("carrier", [
        'gh release create v1 --notes "Release v1.0"',
        'gh gist create f.txt --desc "note"',
    ])
    def test_other_new_carriers_never_leg_merged(self, carrier):
        # release/gist were already span-scoped (7/7b/7c): the F1-class trigger never
        # leg-merged them (NO second under-block). True on base AND pre-fix AND fixed.
        cmd = carrier + ";" + M5 + " --delete-branch"
        assert D_BASE(cmd) is True and D_PREFIX(cmd) is True and D(cmd) is True


class TestOverAnchoring:
    """The F1 fix's git-tag anchor is a NON-gobbling word-class prefix (excludes ;&|), so it
    cannot cross an unquoted separator into a LATER `git tag` and re-strip an earlier leg's
    -m value. The pre-fix whole-command 7d DID (it matched the trailing `git tag` and
    stripped `git commit`'s -m, leg-merging the gh leg). base=True / pre-fix=False / fixed=True."""

    def test_over_anchoring_witness_closed(self):
        cmd = 'git commit -m "x";gh pr merge 5;git tag v1'
        assert D_BASE(cmd) is True
        assert D_PREFIX(cmd) is False, "pre-fix gobbler MUST leg-merge (else the over-anchor guard is vacuous)"
        assert D(cmd) is True, "fixed non-gobbling anchor must keep the gh leg caught"


class TestNewCarrierAxisCoverage:
    """MINOR-1 (review): the strip axes exercised on carrier-7 (gh pr) are now exercised on
    ALL 3 NEW carriers (release/gist/git-tag) — they inherit via the shared _strip_flag_values
    /_VALUE_TOKEN, but the cert now GUARDS it per carrier (a regression to one new carrier's
    $()-preserve or exotic-quote handling would now red a row)."""

    # (label, value-flag prefix, tag-name suffix needed after the value)
    _CARRIERS = [
        ("release --notes", 'gh release create v1 --notes ', ''),
        ("gist --desc", 'gh gist create f.txt --desc ', ''),
        ("git-tag -m", 'git tag -m ', ' v1'),
    ]

    @pytest.mark.parametrize("label,prefix,suffix", _CARRIERS)
    def test_cmdsub_preserved_stays_gated(self, label, prefix, suffix):
        # $()/backtick in the value EXECUTES -> must stay gated (base AND HEAD).
        for val in ['"$(%s)"' % PF, '"`%s`"' % PF]:
            cmd = prefix + val + suffix
            assert D_BASE(cmd) is True and D(cmd) is True, "%s: cmd-sub must stay gated: %r" % (label, cmd)

    @pytest.mark.parametrize("label,prefix,suffix", _CARRIERS)
    def test_exotic_quote_over_block_closed_at_head(self, label, prefix, suffix):
        # ANSI-C $'...' and adjacent-concat forms are STRIPPED (over-block closed) at HEAD.
        ansi = prefix + "$'%s'" % PF + suffix
        concat = prefix + '"a "\'%s\'' % M5 + suffix
        assert D(ansi) is False, "%s: $'...' over-block not closed: %r" % (label, ansi)
        assert D(concat) is False, "%s: adjacent-concat over-block not closed: %r" % (label, concat)

    @pytest.mark.parametrize("label,prefix,suffix", _CARRIERS)
    def test_spaced_compound_tail_caught(self, label, prefix, suffix):
        # Leg-locality (spaced separator): a destructive tail after the carrier stays caught.
        cmd = prefix + '"x"' + suffix + " && " + PF
        assert D(cmd) is True, "%s: spaced compound tail lost: %r" % (label, cmd)
