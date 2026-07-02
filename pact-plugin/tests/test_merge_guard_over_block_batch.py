"""Batch-level TEST-phase certification for the merge-guard over-block batch
(#1064 lease-push fold + presence-bind, #1077 httpie un-gating, #1078 cross-leg
flag-leak union-arm anchoring).

These are the TEST-phase ADDITIONS on top of the per-lane verification suites —
they cover the composition surface the single-lane tests do not:
  * faithful lease spellings across flag PLACEMENT (not just the bare/=value
    spellings the lane suite pinned) — every one must gate AND mint;
  * the #1064 x #1069 composition — a faithful lease approval must still
    AUTHORIZE the SAME command carrying a benign continuation leg;
  * the #1078 cure across the full separator/placement matrix (extends the
    lane suite's &&/;/| rows);
  * the under-block guard — first-leg danger + a benign continuation must STILL
    gate (the anchoring narrows to the first leg, it must not stop gating a
    first-leg destructive op just because a benign leg follows);
  * cross-lane interaction rows (lease x lane3, lease x httpie).

Every assertion here is the SAFE direction: faithful single-command clicks
mint/authorize; benign compounds run free; first-leg-destructive forms stay
gated. No test here pins an accepted residual as contract — pinning would
cement it: the intra-lease value-variation residual and the =false negation
corner are documented via the lane suites' existing tripwire pins ONLY. The
mint-vs-read bound_flags surface asymmetry (#1083 — the mint scanned the full
option text while the read side isolated the single destructive leg, so a
privileged-flag literal in a benign continuation leg DENIED that faithful
compound) was subsequently FIXED in-batch: the mint scan is leg-bounded and the
read bind gained a two-tier fallback, with canaries in
test_merge_guard_auth_symmetry.py::TestLegBoundedMintWindow.

Sibling per-lane suites (do not duplicate):
  * test_merge_guard.py::TestLeaseToDefaultGateAndMint (Lane 1 envelope)
  * test_merge_guard_privileged_flags.py::TestLeaseNegationResidualTripwire (=false)
  * test_merge_guard_op_recognition_completeness.py::TestCrossLegFlagLeakOverBlockGone,
    ::TestHttpieMembershipCompleteness, ::TestAcceptedRecognitionLimitationPins
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.merge_guard_common import (  # noqa: E402
    is_dangerous_command as D,
    detect_command_operation_type as OP,
    extract_privileged_flags as EPF,
)


# ---------------------------------------------------------------------------
# Real mint -> execute round-trip helpers (envelope layer), mirroring the
# TestLeaseToDefaultGateAndMint idiom: mint a SINGLE-command approval, then run
# a (possibly continuation-carrying) command through the real pre hook.
# ---------------------------------------------------------------------------

def _mint_single(cmd: str, tmp_path) -> int:
    """Drive the REAL post hook with an approval whose clicked option embeds a
    SINGLE `cmd`; return the count of tokens minted."""
    from merge_guard_post import main as post_main

    envelope = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": "Proceed?",
            "options": [
                {"label": "Yes", "description": f"Run `{cmd}`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        }]},
        "tool_response": {"answers": {"Proceed?": "Yes"}},
        "session_id": "batch-test-session",
    })
    with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
         patch("sys.stdin", io.StringIO(envelope)):
        with pytest.raises(SystemExit) as exc_info:
            post_main()
    assert exc_info.value.code == 0
    return len(list(tmp_path.glob("merge-authorized-*")))


def _execute(cmd: str, tmp_path) -> int:
    """Run `cmd` through the REAL pre hook main(); return its exit code
    (0 = ALLOW, 2 = DENY)."""
    from merge_guard_pre import main as pre_main

    envelope = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": "batch-test-session",
    })
    with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
         patch("sys.stdin", io.StringIO(envelope)), \
         patch("sys.stdout", io.StringIO()):
        with pytest.raises(SystemExit) as exc_info:
            pre_main()
    return exc_info.value.code


# ===========================================================================
# Lane 1 (#1064) — faithful lease spellings across flag placement
# ===========================================================================

class TestLeaseFaithfulPlacementVariants:
    """A faithful lease push to a default branch must gate AND mint regardless of
    where the `--force-with-lease` flag sits relative to other flags/positionals.
    The lane suite pins bare / master / =value; this pins the PLACEMENT axis
    (global flags before, order swaps, trailing flag) so the fold cannot silently
    regress on a real-world spelling. Every row is a faithful single command."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push -u --force-with-lease origin main",          # global flag before
            "git push --force-with-lease -v origin main",          # flag between
            "git push --force-with-lease origin main --verbose",   # trailing benign flag
            "git push origin main --force-with-lease",             # trailing lease flag
            "git push --force-with-lease origin master",           # master target
        ],
    )
    def test_placement_variants_detect_push_to_main(self, cmd):
        assert OP(cmd) == "push-to-main", f"lease placement variant lost recognition: {cmd!r}"
        assert D(cmd) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push -u --force-with-lease origin main",
            "git push --force-with-lease -v origin main",
            "git push origin main --force-with-lease",
        ],
    )
    def test_placement_variants_bind_the_presence_flag(self, cmd):
        """The presence bind must fire on every placement (else a plain-push token
        could authorize the lease push — the #1042 separation would silently fail)."""
        assert EPF(cmd, "push-to-main") == ["--force-with-lease"]


class TestLeaseBenignContinuationComposition:
    """#1064 x #1069: a faithful lease approval (SINGLE command) must AUTHORIZE the
    same command carrying a BENIGN continuation leg — the read side isolates the
    single destructive leg, so appending `&& echo ...` / a redirect must not break
    the authorization the fold un-blocked. The lane envelope suite only exercises
    the byte-identical case; this is the composition with the benign-continuation
    guarantee."""

    LEASE = "git push --force-with-lease origin main"

    @pytest.mark.parametrize(
        "executed",
        [
            "git push --force-with-lease origin main && echo pushed",
            "git push --force-with-lease origin main ; echo pushed",
            "git push --force-with-lease origin main > push.log",
            "git push --force-with-lease origin main && git status",
        ],
    )
    def test_lease_approval_authorizes_with_benign_continuation(self, executed, tmp_path):
        assert _mint_single(self.LEASE, tmp_path) == 1
        assert _execute(executed, tmp_path) == 0, (
            f"benign continuation broke the faithful lease authorization: {executed!r}"
        )

    def test_lease_approval_authorizes_byte_identical_control(self, tmp_path):
        """Non-vacuity control: the bare byte-identical execution authorizes, so a
        DENY in the continuation rows above would isolate to the continuation, not a
        broken token."""
        assert _mint_single(self.LEASE, tmp_path) == 1
        assert _execute(self.LEASE, tmp_path) == 0


# ===========================================================================
# Lane 3 (#1078) — cross-leg leak cure across the full separator/placement matrix
# ===========================================================================

class TestCrossLegLeakSeparatorAndPlacementMatrix:
    """The #1078 cure (first-leg anchoring of the flag-condition union arm) must
    hold across every separator and placement, not just the &&/;/| the lane suite
    pins. Each row is a benign first-leg op with a destructive-LOOKING flag/verb in
    a NON-first leg — all must run FREE (D is False). Extends
    TestCrossLegFlagLeakOverBlockGone.

    Every row here is COUPLED to the anchoring (verified: each flips back to gated
    under the identity-prefix mutation that simulates the pre-fix whole-command
    feed — the same non-vacuity mutation TestCrossLegFlagLeakOverBlockGone runs).
    Rows whose ungating comes from a DIFFERENT mechanism (quote stripping) live in
    test_quoted_danger_flag_in_benign_leg_runs_free below, not here."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature & rm -rf build/",              # single & (background)
            "git push origin feature |& rm -rf build/",            # |& pipe-both
            "git push origin feature && rm -rf build/ && echo ok",  # 3-leg
            "echo start && git push origin feature && rm -rf x",    # push is a MIDDLE leg
            "git push 'origin' feature && rm -rf build/",           # quoted positional, first leg
            'git push origin "feat&&ure" && rm -rf build/',         # quoted metachar, first leg
        ],
    )
    def test_cross_leg_leak_stays_cured(self, cmd):
        assert D(cmd) is False, f"cross-leg flag leak re-appeared (OVER-BLOCK): {cmd!r}"

    def test_quoted_danger_flag_in_benign_leg_runs_free(self):
        """DEFENSE-IN-DEPTH (distinct mechanism from the anchoring): a QUOTED danger
        flag echoed in a benign leg runs free because `_strip_non_executable_content`
        removes the quoted string BEFORE the literal floor sees it — NOT because of
        first-leg anchoring (this row stays ungated even under the identity-prefix
        mutation). Kept separate so the cure class above is honestly anchoring-coupled."""
        assert D('git push origin feature && echo "--force"') is False

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            ("git push origin feature && git branch -D other", "branch-delete"),
            ("git push origin feature && gh pr close 9 --delete-branch", "close"),
        ],
    )
    def test_real_destructive_op_in_later_leg_still_gates(self, cmd, expected_op):
        """DISCRIMINATION CONTRAST — NOT an over-block. When the NON-first leg holds
        a GENUINELY destructive op (idiomatic `git branch -D` / `gh pr close
        --delete-branch`), the LITERAL floor gates it match-anywhere BY DESIGN (SSOT
        header NB), and it is authorizable via the read side's single-destructive-leg
        isolation. This differs from BOTH the cured benign-leak rows above (where the
        later leg is a benign `rm`/`echo` whose flag leaked into the union arm) AND
        the #1082 over-block residual (a benign push mislabeled force-push by a later
        `rm -f`/`rm --force`) — here a real destructive op is present, so gating is
        correct, not a faithful-click over-block."""
        assert D(cmd) is True, f"UNDER-BLOCK: real destructive later leg stopped gating: {cmd!r}"
        assert OP(cmd) == expected_op


class TestFirstLegDangerWithContinuationStillGates:
    """UNDER-BLOCK GUARD (non-vacuity for the anchoring): a destructive op in the
    FIRST leg must STILL gate with the correct op-class even when a BENIGN
    continuation follows. First-leg anchoring narrows recognition to the first
    executable leg — it must not stop gating a first-leg destructive form. These
    are the union-arm-only spellings (clustered / split / short flags the literal
    floor alone would miss), so a regression here is a real under-block."""

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            ("git branch -Df temp && echo done", "branch-delete"),
            ("git branch -fD temp ; echo done", "branch-delete"),
            ("git branch --delete -f temp && echo done", "branch-delete"),
            ("gh pr close 5 -d && echo done", "close"),
            ("gh pr close 5 -d | tee log", "close"),
            ("git branch -Df temp && rm -rf build/", "branch-delete"),
        ],
    )
    def test_first_leg_danger_gates_despite_continuation(self, cmd, expected_op):
        assert D(cmd) is True, f"UNDER-BLOCK: first-leg danger stopped gating: {cmd!r}"
        assert OP(cmd) == expected_op


# ===========================================================================
# Cross-lane composition
# ===========================================================================

class TestBatchCrossLaneComposition:
    """Interaction rows spanning two lanes' surfaces in one command — the batch is
    only correct if the lanes compose without one re-opening another's over-block
    or masking another's cure."""

    @pytest.mark.parametrize(
        "cmd,exp_d,exp_op",
        [
            # lease-to-default (Lane 1) as the first leg + a benign continuation
            # (Lane 3 territory): still gated AND recognized -> mintable via the
            # single-leg approval (no gated-but-unmintable state).
            ("git push --force-with-lease origin main && rm -rf build/", True, "push-to-main"),
            ("git push origin main && rm -rf build/", True, "push-to-main"),
            # lease-to-TOPIC (must NOT gate) + a continuation: the fold must not
            # widen beyond the default branch even in a compound.
            ("git push --force-with-lease origin feature && rm -rf build/", False, None),
            # lease-to-default first leg + an httpie (Lane 2, ungated) leg: the
            # lease still gates+mints; httpie contributes nothing.
            ("git push --force-with-lease origin main && http DELETE api.github.com/x/git/refs",
             True, "push-to-main"),
            # close sibling (Lane 3 cured) three-leg with a benign rm: runs free.
            ("gh pr close 42 && git branch -d temp && rm -rf build/", False, "close"),
        ],
    )
    def test_cross_lane_rows(self, cmd, exp_d, exp_op):
        assert D(cmd) is exp_d, f"cross-lane composition changed gating: {cmd!r}"
        assert OP(cmd) == exp_op


# ===========================================================================
# Lane 2 (#1077) — adversarial httpie spellings (extends the membership suite)
# ===========================================================================

class TestHttpieAdversarialSpellings:
    """Additional httpie spellings beyond the lane membership suite — all must be
    ungated (D is False) AND unclassified (OP is None), keeping httpie wholly out
    of charter (#1077/#1079). If any gates, a second httpie read site exists or a
    removed arm regrew."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "http --auth user:pass DELETE https://api.github.com/repos/o/r/git/refs/heads/f",
            "http -v PUT https://api.github.com/repos/o/r/pulls/42/merge",
            "https PATCH api.github.com/repos/o/r/git/refs/heads/f",   # https alias, no scheme
            "http Delete https://api.github.com/repos/o/r/git/refs/heads/f",  # mixed-case method
            "http PUT api.github.com/repos/o/r/git/refs/heads/f && rm -rf build/",  # httpie x lane3
        ],
    )
    def test_httpie_spelling_stays_ungated_and_unclassified(self, cmd):
        assert D(cmd) is False, f"httpie re-gated (over-block): {cmd!r}"
        assert OP(cmd) is None, f"httpie re-classified in mint: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "wget --method=DELETE https://api.github.com/repos/o/r/git/refs/heads/f",
            "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/f",
        ],
    )
    def test_idiomatic_clients_still_gate_contrast(self, cmd):
        """Discriminating contrast: the idiomatic API clients stay gated, so the
        all-ungated httpie rows are a real membership fact, not a broken probe."""
        assert D(cmd) is True, f"idiomatic API client stopped gating: {cmd!r}"
