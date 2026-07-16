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


# =========================================================================================
# R1 / F2 — PUSH/BRANCH DETECT ARMS OFF THE MASKED VIEW (remediation commit 5). Commit 2
# ran the force-push / push-to-main / branch-delete arms on the quote-masked view; a
# COSMETICALLY-QUOTED remote (`git push 'origin' --delete main` — the quotes are cosmetic,
# the token EXECUTES) masks to a whitespace gap that push-to-main's POSITIONAL regex binds
# ACROSS, downgrading the op (remote-ref-delete -> push-to-main) and short-circuiting the
# raw delete-class extractors. LAUNDERING: a benign `git push origin main` approval minted
# a push-to-main token that authorized the quoted-remote DELETE within TTL.
#
# THE FIX IS GENERAL (structural), not a per-flag guard: those arms run on RAW in BOTH
# passes (their certified base surface); ONLY the gh-pr merge/close prose arms stay on the
# view. A per-flag delete-class guard would have to enumerate the disqualifying flag
# surface completely and would still miss the quoted-'--force' spelling.
# =========================================================================================
_R1_PUSH = "git push "
_R1_DELETE_CLASS = [
    # (label, command, restored op) — base == remediated (the launder rows return to base)
    ("sq-remote-delete", _R1_PUSH + "'origin' --delete main", "remote-ref-delete"),
    ("dq-remote-d", _R1_PUSH + '"origin" -d main', "remote-ref-delete"),
    ("sq-remote-d", _R1_PUSH + "'origin' -d main", "remote-ref-delete"),
    ("sq-remote-prune", _R1_PUSH + "'origin' --prune main", "remote-mass-delete"),
    ("sq-remote-mirror", _R1_PUSH + "'origin' --mirror main", "remote-mass-delete"),
    ("sq-remote-delete-HEAD-colon", _R1_PUSH + "'origin' --delete HEAD:main", "remote-ref-delete"),
    ("global-flag-prefix", "git -c x=y push 'origin' --delete main", "remote-ref-delete"),
    ("lease-sq-remote-delete", _R1_PUSH + "--force-with-lease 'origin' --delete main", "remote-ref-delete"),
    # quoted-FLAG spellings (the class members a per-flag guard would miss):
    ("quoted-force-flag", _R1_PUSH + "'--force' origin main", "force-push"),
    ("quoted-delete-flag", _R1_PUSH + "origin '--delete' main", "remote-ref-delete"),
]
_R1_FAITHFUL = [
    # (label, command, op) — must classify, MINT, and round-trip (the PRIMARY gate)
    ("faithful-push-main", _R1_PUSH + "origin main", "push-to-main"),
    ("faithful-push-master", _R1_PUSH + "origin master", "push-to-main"),
    ("faithful-lease", _R1_PUSH + "--force-with-lease origin main", "push-to-main"),
    ("faithful-quoted-remote-plain", _R1_PUSH + "'origin' main", "push-to-main"),
    ("faithful-quoted-remote-HEAD", _R1_PUSH + "'origin' HEAD:main", "push-to-main"),
    ("faithful-unquoted-delete", _R1_PUSH + "origin --delete main", "remote-ref-delete"),
]


class TestR1PushArmsOffView:
    def test_launder_class_restored_to_base_op(self):
        base = load_baseline()
        for label, cmd, want in _R1_DELETE_CLASS:
            assert base.detect_command_operation_type(cmd) == want, "%s: base drifted" % label
            assert mgc.detect_command_operation_type(cmd) == want, (
                "%s: quoted-token op-downgrade re-opened (launder)" % label
            )
            assert mgc.is_dangerous_command(cmd) is True, "%s: lost gating" % label

    def test_faithful_set_mints_and_roundtrips(self):
        # PRIMARY GATE: every faithful spelling classifies as at base, MINTS from the
        # canonical option shape, and its token authorizes the command.
        base = load_baseline()
        for label, cmd, want in _R1_FAITHFUL:
            assert base.detect_command_operation_type(cmd) == want, "%s: base drifted" % label
            assert mgc.detect_command_operation_type(cmd) == want, (
                "%s: faithful op changed (over-block risk)" % label
            )
            question = {
                "question": "Proceed?",
                "options": [{"label": "Yes", "description": "Run `%s` now" % cmd}],
                "multiSelect": False,
            }
            ctx, refusal = merge_guard_post._mint_context_from_bundle(
                [question], {"Proceed?": "Yes"}
            )
            assert ctx is not None, "%s: faithful click STOPPED MINTING (%s)" % (label, refusal)
            assert merge_guard_pre._token_matches_command({"context": ctx}, cmd) is True, (
                "%s: minted token no longer authorizes its own command" % label
            )

    def test_e2e_launder_closed(self, tmp_path):
        # the confirmed end-to-end launder: a benign push-to-main token must REFUSE the
        # quoted-remote delete (op mismatch restored). Both directions of the round-trip.
        question = {
            "question": "Proceed?",
            "options": [{"label": "Yes", "description": "Run `git push origin main` now"}],
            "multiSelect": False,
        }
        ctx, refusal = merge_guard_post._mint_context_from_bundle(
            [question], {"Proceed?": "Yes"}
        )
        assert ctx is not None and ctx["operation_type"] == "push-to-main"
        merge_guard_post.write_token(ctx, token_dir=tmp_path)
        for victim in (_R1_PUSH + "'origin' --delete main", _R1_PUSH + '"origin" -d main'):
            assert merge_guard_pre.check_merge_authorization(victim, tmp_path) is not None, (
                "LAUNDER OPEN: a benign push-to-main token authorized %r" % victim
            )

    def test_bite_and_wrapped_merge_unchanged(self):
        # the gh-pr prose arms stay on the view: the #1155 bite fix and the wrapped-merge
        # raw fallback both survive the revert.
        assert mgc.detect_command_operation_type(BITE) == "close"
        assert mgc.detect_command_operation_type("bash -c 'gh pr " + MERGE_WORD + " 5'") == "merge"
        assert mgc.detect_command_operation_type('sh -c "gh pr ' + MERGE_WORD + ' 5"') == "merge"

    def test_prose_carrier_pins_stay_not_dangerous(self):
        # With the arms on raw, a push literal carried in PROSE stays not-dangerous:
        #   - git log --grep: the #1181 read-verb strip owns that closure — it is
        #     INDEPENDENT of detect's arm surfaces (base was True; the strip, not the
        #     view move, closed it — documented so a future reader knows this revert
        #     did not touch it).
        #   - commit -m / pr body: carriers 5/7 strip the prose (base behavior,
        #     inherited by the revert, pinned explicitly).
        pf = "git push " + "--for" + "ce origin main"
        base = load_baseline()
        grep_cmd = "git log --grep '%s'" % pf
        assert base.is_dangerous_command(grep_cmd) is True   # the #1181 over-block at base
        assert mgc.is_dangerous_command(grep_cmd) is False   # closed by the strip, not the view
        for cmd in ('git commit -m "%s"' % pf, 'gh pr create --body "%s"' % pf):
            assert base.is_dangerous_command(cmd) is False
            assert mgc.is_dangerous_command(cmd) is False

    def test_r1_monotonicity_no_new_over_block(self):
        # No base-False -> HEAD-True anywhere in the R1 rows; no push/branch op-class
        # differs from base (the revert restores the certified base surface).
        base = load_baseline()
        for label, cmd, _ in _R1_DELETE_CLASS + _R1_FAITHFUL:
            b, h = base.is_dangerous_command(cmd), mgc.is_dangerous_command(cmd)
            assert not (b is False and h is True), "new over-block on %s" % label
            assert base.detect_command_operation_type(cmd) == mgc.detect_command_operation_type(cmd), (
                "push/branch op-class differs from base on %s" % label
            )
