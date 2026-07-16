"""
Location: pact-plugin/tests/test_merge_guard_1155_cert.py
Summary: BIDIRECTIONAL certification for the cross-auth recognition fix (detect on the
         executed-surface view with raw fallback) + the find -name/-path carrier.
         TWO-LAYER ORACLE: (1) unit base-vs-HEAD differentials over the REAL
         `detect_command_operation_type` / `is_dangerous_command` (baseline from the
         committed vendored fixture via merge_guard_baseline_loader — loud-fail,
         CI-executable); (2) END-TO-END `check_merge_authorization` with a live
         token fixture — the observed symptom lives in the token-mismatch path, not
         the unit classifier, so the e2e row is the one that reproduces the issue.

         THE BITE (cross-auth class): `gh pr close 5 --comment '…gh pr merge 5
         --admin…'` — the comment PROSE classified merge at base, so (a) the read
         side bound (merge,5) and any close token the operator minted mismatched
         (the faithful close was permanently gated), and (b) approving the close
         MINTED a merge+admin credential (the launder). On the view the prose is
         masked: both sides bind (close,5); a (close,5) token authorizes end-to-end.

         V2 FALLBACK DISCRIMINATORS: `bash -c 'gh pr merge 5'` / `sh -c "gh pr
         merge 5"` classify None on the view (payload masked) but are
         currently-minting faithful spellings — the raw fallback pass keeps them
         merge. A future "simplify detect to view-only" edit fails exactly here
         (and in test_merge_guard_mint_parity.py).

         DELIBERATE DISTRACTOR POLICY (pinned, not a regression): an option whose
         text embeds BOTH the close command and its merge-naming comment as separate
         regions now yields two distinct (op, target) pairs -> the multiplicity gate
         refuses the mint with `multiple_commands`. The click's path to execution is
         a token minted for the close spelling (single-region option shape, also
         pinned) or a comment reworded without the embedded command.

         Destructive verbs are assembled at runtime so this file stays inert to the
         live guard.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

import shared.merge_guard_common as mgc  # noqa: E402
import merge_guard_pre  # noqa: E402
import merge_guard_post  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402

DETECT = mgc.detect_command_operation_type
D = mgc.is_dangerous_command

# --- literals assembled at runtime (inert to the live guard) ---
M5 = "gh " + "pr " + "merge 5 --admin"
MERGE_WORD = "mer" + "ge"
BITE = (
    "gh pr close 5 --comment 'weighed gh pr " + MERGE_WORD
    + " 5 --admin but closing instead'"
)


# =========================================================================================
# LAYER 1a — unit detect differential (base vs HEAD).
# =========================================================================================
DETECT_ROWS = [
    # the bite: recognition REFINES to the executed surface (merge -> close).
    ("bite-refines-to-close", BITE, "merge", "close"),
    ("plain-close-control", "gh pr close 5 --comment 'done'", "close", "close"),
    # V2 fallback discriminators: wrapped faithful spellings keep classifying.
    ("wrapped-bash-c", "bash -c 'gh pr " + MERGE_WORD + " 5'", "merge", "merge"),
    ("wrapped-sh-c-dq", 'sh -c "gh pr ' + MERGE_WORD + ' 5"', "merge", "merge"),
    ("plain-merge", "gh pr " + MERGE_WORD + " 5", "merge", "merge"),
    # D3 pins: API-URL arms stay RAW both passes — a quoted gh-api URL keeps
    # classifying (and therefore minting).
    (
        "quoted-api-url-delete",
        "gh api 'repos/o/r/git/refs/heads/x' -X DELETE",
        "branch-delete",
        "branch-delete",
    ),
    (
        "global-flag-gh-api",
        "gh -R o/r api repos/o/r/git/refs/heads/x -X DELETE",
        "branch-delete",
        "branch-delete",
    ),
    # extraction-coupled arm stays RAW: pulls/<N>/merge endpoint per-leg.
    ("api-merge-endpoint", "gh api -X PUT /repos/o/r/pulls/42/" + MERGE_WORD, "merge", "merge"),
    # D2 pin: quoted CLI target keeps classifying (extraction stays raw).
    ("branch-delete-quoted-target", 'git branch -D "feat/x"', "branch-delete", "branch-delete"),
]


class TestDetectDifferential:
    @pytest.mark.parametrize(
        "label,cmd,expect_base,expect_head", DETECT_ROWS, ids=[r[0] for r in DETECT_ROWS]
    )
    def test_detect_base_vs_head(self, label, cmd, expect_base, expect_head):
        assert load_baseline().detect_command_operation_type(cmd) == expect_base
        assert DETECT(cmd) == expect_head


# =========================================================================================
# LAYER 1b — unit is_dangerous differential: find carrier closure + retention +
# declared residuals + carrier-10-closed grep-class regression pins.
# =========================================================================================
FIND_CLOSURES = [
    ("find-name", "find . -name '%s'" % M5),
    ("find-path", "find . -path '%s'" % M5),
]
FIND_RETENTION = [
    # an executing find PRIMARY anywhere in the span disables the strip entirely —
    # including when the danger sits in the -name value (fail-safe over-block
    # direction, ratified).
    ("find-exec-danger-in-exec", "find . -name 'x' -exec sh -c '%s' \\;" % M5),
    ("find-exec-danger-in-name", "find . -name '%s' -exec cat {} \\;" % M5),
    # _VERB_MSG_BODY stops at the unquoted | — the executing tail stays outside.
    ("find-pipe-tail", "find . -name '%s' | sh" % M5),
]
DECLARED_RESIDUALS = [
    # masked-danger-scan trap pins: these EXECUTE their strings; no view-based fix
    # may free them (proven base-True->False under the naive masked scan).
    ("awk-residual", "awk '/%s/ {print}' f" % M5),
    ("python-c-residual", "python -c \"print('%s')\"" % M5),
    # -iname is a deliberate vocabulary exclusion (extend only with own cert rows).
    ("find-iname-residual", "find . -iname '%s'" % M5),
]
GREP_CLASS_REGRESSION = [
    # closed upstream (positional over-block class) — must STAY closed (False on both).
    ("grep-rn", "grep -rn '%s' ." % M5),
    ("grep-c", "grep -c '%s' file.txt" % M5),
]


class TestFindCarrierAndResiduals:
    @pytest.mark.parametrize("label,cmd", FIND_CLOSURES, ids=[r[0] for r in FIND_CLOSURES])
    def test_find_value_over_block_closed(self, label, cmd):
        assert load_baseline().is_dangerous_command(cmd) is True, "not a genuine over-block at base"
        assert D(cmd) is False, "faithful find click still gated at HEAD"

    @pytest.mark.parametrize("label,cmd", FIND_RETENTION, ids=[r[0] for r in FIND_RETENTION])
    def test_executing_find_forms_stay_caught(self, label, cmd):
        assert load_baseline().is_dangerous_command(cmd) is True
        assert D(cmd) is True, "fix opened an under-block on an executing find form"

    @pytest.mark.parametrize(
        "label,cmd", DECLARED_RESIDUALS, ids=[r[0] for r in DECLARED_RESIDUALS]
    )
    def test_declared_residuals_unchanged(self, label, cmd):
        assert load_baseline().is_dangerous_command(cmd) is True
        assert D(cmd) is True, "a declared residual was freed — no view/strip fix may touch it"

    @pytest.mark.parametrize(
        "label,cmd", GREP_CLASS_REGRESSION, ids=[r[0] for r in GREP_CLASS_REGRESSION]
    )
    def test_grep_class_stays_closed(self, label, cmd):
        assert load_baseline().is_dangerous_command(cmd) is False
        assert D(cmd) is False


# =========================================================================================
# LAYER 1c — extraction symmetry on the bite: extract_command_context inherits the
# refined op through the shared detect call (its body is unchanged; extraction raw).
# =========================================================================================
class TestBiteExtraction:
    def test_head_binds_close(self):
        ctx = mgc.extract_command_context(BITE)
        assert ctx.get("operation_type") == "close"
        assert ctx.get("pr_number") == "5"
        assert ctx.get("bound_flags") == []

    def test_base_bound_merge_the_cross_auth(self):
        ctx = load_baseline().extract_command_context(BITE)
        assert ctx.get("operation_type") == "merge", (
            "baseline discriminator: at base the comment prose bound the WRONG op"
        )


# =========================================================================================
# LAYER 2 — END-TO-END check_merge_authorization with a live token fixture. THE row
# that reproduces the issue: a (close, 5, []) token — what a faithful operator's
# close approval mints — authorizes the bite at HEAD and MISMATCHED at base.
# =========================================================================================
class TestEndToEndTokenOracle:
    CLOSE_TOKEN_CTX = {"operation_type": "close", "pr_number": "5", "bound_flags": []}

    def test_close_token_authorizes_bite_at_head(self, tmp_path):
        merge_guard_post.write_token(dict(self.CLOSE_TOKEN_CTX), token_dir=tmp_path)
        assert merge_guard_pre.check_merge_authorization(BITE, tmp_path) is None, (
            "a faithful close token no longer authorizes the faithful close click"
        )

    def test_close_token_mismatched_at_base(self, tmp_path, monkeypatch):
        # Run HEAD's auth logic with the BASELINE classifier semantics: patch the
        # from-imported classifier seams merge_guard_pre consumes. At base the
        # command derives (merge,5), so the (close,5) token REFUSES — the observed
        # symptom (any token the user minted for the close spelling mismatched).
        baseline = load_baseline()
        monkeypatch.setattr(
            merge_guard_pre, "extract_command_context", baseline.extract_command_context
        )
        monkeypatch.setattr(
            merge_guard_pre, "is_dangerous_command", baseline.is_dangerous_command
        )
        monkeypatch.setattr(
            merge_guard_pre, "_single_destructive_leg", baseline._single_destructive_leg
        )
        monkeypatch.setattr(
            merge_guard_pre, "_single_detectable_leg", baseline._single_detectable_leg
        )
        merge_guard_post.write_token(dict(self.CLOSE_TOKEN_CTX), token_dir=tmp_path)
        assert merge_guard_pre.check_merge_authorization(BITE, tmp_path) is not None, (
            "vacuity: the pre-fix cross-auth mismatch did not reproduce at base"
        )

    def test_bite_still_gated_without_token(self, tmp_path):
        # close is a destructive op: with NO token the bite stays gated (the fix
        # changes WHICH token authorizes, never whether approval is required).
        assert merge_guard_pre.check_merge_authorization(BITE, tmp_path) is not None


# =========================================================================================
# MINT SIDE — the launder discriminator and the deliberate distractor policy.
# Option shape matters: a bare-text option carries the comment prose AND the full
# command as two regions; a backtick-wrapped option carries one region.
# =========================================================================================
class TestMintSide:
    BARE_Q = {
        "question": "Proceed?",
        "options": [{"label": "Yes, close it", "description": BITE}],
        "multiSelect": False,
    }
    BACKTICK_Q = {
        "question": "Proceed?",
        "options": [{"label": "Yes, close it", "description": "Run `%s` now" % BITE}],
        "multiSelect": False,
    }
    ANSWER = {"Proceed?": "Yes, close it"}

    def test_base_minted_the_launder(self, monkeypatch):
        # PRE-FIX DISCRIMINATOR: both regions classified merge at base -> ONE pair
        # (merge,5) -> approving a CLOSE minted a merge+admin credential.
        baseline = load_baseline()
        monkeypatch.setattr(
            merge_guard_post, "detect_command_operation_type",
            baseline.detect_command_operation_type,
        )
        monkeypatch.setattr(
            merge_guard_post, "extract_command_context", baseline.extract_command_context
        )
        monkeypatch.setattr(
            merge_guard_post, "locate_command_regions", baseline.locate_command_regions
        )
        ctx, refusal = merge_guard_post._mint_context_from_bundle([self.BARE_Q], self.ANSWER)
        assert refusal is None and ctx is not None
        assert ctx["operation_type"] == "merge", "launder discriminator"
        assert "--admin" in ctx.get("bound_flags", [])

    def test_head_refuses_bare_shape_multiplicity(self):
        # DELIBERATE DISTRACTOR POLICY: two distinct pairs {(merge,5),(close,5)}
        # -> refusal. NOT an over-block regression — the faithful path is the
        # single-region option (below) or a reworded comment.
        ctx, refusal = merge_guard_post._mint_context_from_bundle([self.BARE_Q], self.ANSWER)
        assert ctx is None
        assert refusal == "multiple_commands"

    def test_head_mints_close_on_single_region_shape(self):
        # the faithful close path: the canonical backtick-wrapped option is ONE
        # region -> mints the CLOSE credential that the e2e row proves authorizes.
        ctx, refusal = merge_guard_post._mint_context_from_bundle(
            [self.BACKTICK_Q], self.ANSWER
        )
        assert refusal is None and ctx is not None
        assert ctx["operation_type"] == "close"
        assert ctx["pr_number"] == "5"
        assert ctx.get("bound_flags") == []
