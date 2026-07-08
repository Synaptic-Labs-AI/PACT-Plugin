"""
Location: pact-plugin/tests/test_merge_guard_1129_r1_cert.py
Summary: TARGETED certification for #1129 R1 — the additive multi-branch FORCE-delete
         mint widening (branch_set identity). This is the ADVERSARIAL cert layer on
         top of the coder's functional-verification file
         (test_merge_guard_1129_r1_branch_set.py); it does NOT duplicate that file.

         The BACKBONE here is the REAL mint->execute round-trip: every load-bearing row
         drives the actual post_main (AskUserQuestion approval -> token mint) then the
         actual pre_main (Bash exec -> allow/deny), asserting BOTH the mint count AND the
         execute outcome. This end-to-end layer is what the plan made REQUIRED for R1,
         because the bug it guards is a MINT-SIDE binding gap that is invisible to any
         read-side (extract_command_context / _token_matches_command) check: R1 originally
         shipped with branch_set registered in extract_command_context + the read arm but
         MISSING from _target_value (the #1064 mint-side four-site element), so a faithful
         multi-branch click minted ZERO tokens and was DENIED (gated-but-unmintable) while
         every read-side unit test stayed green. The mint-site fix (register branch_set in
         _target_value) closed it; TestNonVacuity below pins each of the three sites
         (extract / mint / read) as INDEPENDENTLY load-bearing so the class can never
         regress silently again.

         Scope (right-sized, NOT the #1118 bidirectional): R1 is over-block-only + additive
         (no shared-strip touch), so the cert is over-block cure + the #1032 under-block
         (set-equality) preservation, proven by real round-trip + non-vacuity. Confirmed
         mode-INVARIANT: every function on the branch_set path (_extract_branch_delete_set,
         extract_command_context, _target_value, _token_matches_command's branch_set arm)
         takes only a command / context string — no session_id / leadSessionId parameter —
         so the classification cannot diverge by teammateMode.
Used by: pytest (merge-guard suite).

The `git branch -D` force-delete verb is assembled at runtime (D = "-"+"D"), so this
file carries no raw force-delete literal — mirrors the sibling verification file and
keeps the file inert to any literal-scanning tool (and to the live merge guard).

Module-level imports are deliberately limited to STABLE symbols (post_main, pre_main,
the hook modules, extract_command_context) so that a COMBINED source-only revert of the
R1 change (which removes the R1-only symbol _extract_branch_delete_set) does not turn the
forward round-trip rows into a collection ImportError — those rows must FAIL cleanly
(minted=0 / DENY), which is the non-vacuity signal. R1-only symbols are touched ONLY
inside the TestNonVacuity bodies, via monkeypatch, at run time.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import merge_guard_post  # noqa: E402
import merge_guard_pre  # noqa: E402
import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402

# Force-delete verb assembled at runtime — no raw literal in the source.
_D = "-" + "D"
_GB = "git " + "branch "


def _cmd(flags: str, names: list) -> str:
    return _GB + flags + " " + " ".join(names)


# ---------------------------------------------------------------------------
# REAL mint -> execute round-trip harness (the cert backbone). Drives the actual
# post_main (AskUserQuestion approval whose clicked option embeds the command in
# backticks) and pre_main (Bash exec), both against the SAME token dir, so the
# assertions cover the FULL mint pipeline (_collect_pairs / _target_value /
# token-write) that the read-side unit tests bypass.
# ---------------------------------------------------------------------------
def _mint(cmd: str, tok: Path) -> int:
    """Drive the REAL post hook with an approval whose clicked option embeds `cmd`;
    return the count of tokens minted BY THIS CALL (new files only, so a second mint
    into the same token dir — e.g. a non-vacuity control — is counted independently)."""
    before = set(tok.glob("merge-authorized-*"))
    env = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": "Proceed?",
            "options": [
                {"label": "Yes", "description": "Run `%s`" % cmd},
                {"label": "Cancel", "description": "Abort"},
            ],
        }]},
        "tool_response": {"answers": {"Proceed?": "Yes"}},
        "session_id": "cert-session",
    })
    with patch.object(merge_guard_post, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), \
         patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit as e:
            assert e.code == 0, "post hook exited nonzero: %r" % (e.code,)
    return len(set(tok.glob("merge-authorized-*")) - before)


def _execute(cmd: str, tok: Path, session_id: str = "cert-session") -> int:
    """Run `cmd` through the REAL pre hook; return exit code (0=ALLOW, 2=DENY)."""
    env = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": session_id,
    })
    with patch.object(merge_guard_pre, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), \
         patch("sys.stdout", io.StringIO()), \
         patch("sys.stderr", io.StringIO()):
        try:
            pre_main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 0


def _roundtrip(mint_cmd: str, exec_cmd: str, tok: Path):
    """(minted_count, exec_exit_code) for mint `mint_cmd` then exec `exec_cmd`."""
    minted = _mint(mint_cmd, tok)
    rc = _execute(exec_cmd, tok)
    return minted, rc


ALLOW, DENY = 0, 2
# The five FORCE spellings detect classifies as branch-delete (#1094 per-leg cure).
_FORCE_SPELLINGS = [_D, "-Df", "-fD", "--delete --force", "--force --delete"]


# ===========================================================================
# B9 — the faithful multi-branch click MINTS + self-authorizes (the over-block
# cure), through the REAL hooks. THIS is the layer that catches gated-but-
# unmintable: a minted==0 here is the exact bug the mint-site fix closed.
# ===========================================================================
class TestRealMintExecuteRoundTrip:

    @pytest.mark.parametrize("names", [
        ["aa", "bb"],                 # 2 positionals
        ["aa", "bb", "cc"],           # 3
        ["aa", "bb", "cc", "dd", "ee"],  # N
    ])
    def test_multi_branch_arity_mints_and_authorizes(self, names, tmp_path):
        cmd = _cmd(_D, names)
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "faithful multi-branch click did not mint (gated-but-unmintable)"
        assert rc == ALLOW, "minted token did not authorize its own faithful exec"

    @pytest.mark.parametrize("flags", _FORCE_SPELLINGS)
    def test_all_force_spellings_mint_and_authorize(self, flags, tmp_path):
        cmd = _cmd(flags, ["aa", "bb", "cc"])
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "force spelling %r did not mint a multi-branch token" % flags
        assert rc == ALLOW

    def test_reorder_authorizes_through_real_hooks(self, tmp_path):
        # Mint {aa,bb,cc}; execute a REORDERED delete of the same set -> canonical
        # sort makes it self-authorize end-to-end (order-independence).
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb", "cc"]),
                                _cmd(_D, ["cc", "aa", "bb"]), tmp_path)
        assert minted == 1
        assert rc == ALLOW

    def test_slashed_branch_names_mint_and_authorize(self, tmp_path):
        cmd = _cmd(_D, ["fix/144-a", "fix/144-b"])
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1
        assert rc == ALLOW

    def test_long_delete_force_spelling_round_trips(self, tmp_path):
        cmd = _cmd("--delete --force", ["aa", "bb"])
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1
        assert rc == ALLOW


# ===========================================================================
# B10 — set-EQUALITY through the REAL hooks. A multi-branch token ALWAYS mints
# (minted==1), but authorizes ONLY the byte-identical canonical set: a
# superset/subset/disjoint/scalar delete is DENIED. This is the #1032 multi-
# target under-block, proven end-to-end (never a mint-side miss masquerading as
# a refuse — minted==1 is asserted first).
# ===========================================================================
class TestSetEqualityRoundTrip:

    def test_superset_denies(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]),
                                _cmd(_D, ["aa", "bb", "cc"]), tmp_path)
        assert minted == 1, "the {aa,bb} approval must MINT (the refuse must be a read decision, not a mint miss)"
        assert rc == DENY, "a {aa,bb} token must NOT authorize the {aa,bb,cc} superset (#1032)"

    def test_subset_denies(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb", "cc"]),
                                _cmd(_D, ["aa", "bb"]), tmp_path)
        assert minted == 1
        assert rc == DENY

    def test_disjoint_denies(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]),
                                _cmd(_D, ["cc", "dd"]), tmp_path)
        assert minted == 1
        assert rc == DENY

    def test_reorder_authorizes(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]),
                                _cmd(_D, ["bb", "aa"]), tmp_path)
        assert minted == 1
        assert rc == ALLOW

    def test_duplicate_names_dedup_to_same_set_authorizes(self, tmp_path):
        # exec deletes {aa,aa,bb} == the set {aa,bb}; the {aa,bb} token authorizes it.
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]),
                                _cmd(_D, ["aa", "aa", "bb"]), tmp_path)
        assert minted == 1
        assert rc == ALLOW

    def test_scalar_token_does_not_authorize_a_set_command(self, tmp_path):
        # Single-branch approval (scalar `branch` key) must NOT authorize a multi delete.
        minted, rc = _roundtrip(_cmd(_D, ["solo"]), _cmd(_D, ["aa", "bb"]), tmp_path)
        assert minted == 1  # the single-branch approval DOES mint (scalar path)
        assert rc == DENY

    def test_set_token_does_not_authorize_a_scalar_command(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]), _cmd(_D, ["solo"]), tmp_path)
        assert minted == 1
        assert rc == DENY


# ===========================================================================
# B11 — canonicalization survives the REAL token PERSISTENCE (JSON on disk),
# not just an in-memory context dict. The persisted token's branch_set is a
# canonical STRING (never a list/tuple), and a reordered exec still authorizes.
# ===========================================================================
class TestTokenPersistenceCanonicalization:

    def test_persisted_token_branch_set_is_canonical_string(self, tmp_path):
        minted = _mint(_cmd(_D, ["cc", "aa", "bb"]), tmp_path)
        assert minted == 1
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1
        payload = json.loads(tokens[0].read_text())
        # The token carries the shared-SSOT context; branch_set is the canonical,
        # sorted, NUL-joined STRING (#1135 F1 — NUL is git-forbidden in ref names, so
        # the joined identity is INJECTIVE; a list/tuple would have survived the JSON
        # round-trip as a list and broken str-comparison matching).
        ctx = payload.get("context", payload)
        assert ctx.get("branch_set") == "aa\x00bb\x00cc"
        assert isinstance(ctx.get("branch_set"), str)
        # And a reordered exec of the same set authorizes against the persisted token.
        assert _execute(_cmd(_D, ["bb", "cc", "aa"]), tmp_path) == ALLOW


# ===========================================================================
# B12 — gather correctness. A dash-flag is never counted as a branch, and a
# zero-positional force-delete is unmintable (fail-CLOSED -> denied), never
# silently authorized.
# ===========================================================================
class TestGatherCorrectness:

    def test_dash_flag_is_not_gathered_as_a_branch(self, tmp_path):
        # `-r` (a dash-flag) must be dropped; only the two real names form the set.
        cmd = _cmd(_D + " -r", ["origin/x", "origin/y"])
        assert mgc.extract_command_context(cmd).get("branch_set") == "origin/x\x00origin/y"
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1
        assert rc == ALLOW

    def test_zero_positional_force_delete_is_unmintable_and_denied(self, tmp_path):
        # `git branch -D` with NO branch names: no branch / no branch_set -> no
        # (op,target) pair -> no token; the read side denies (gated, fail-closed).
        cmd = _GB + _D
        assert "branch" not in mgc.extract_command_context(cmd)
        assert "branch_set" not in mgc.extract_command_context(cmd)
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 0
        assert rc == DENY


# ===========================================================================
# Regression pins — the additive change must not disturb the neighbours.
# ===========================================================================
class TestRegressionPins:

    def test_single_branch_scalar_path_unchanged(self, tmp_path):
        cmd = _cmd(_D, ["solo"])
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1
        assert rc == ALLOW

    @pytest.mark.parametrize("flags", ["-d", "--delete"])
    @pytest.mark.parametrize("names", [["solo"], ["aa", "bb", "cc"]])
    def test_lowercase_delete_stays_ungated(self, flags, names, tmp_path):
        # Non-force merged-branch delete is NOT dangerous at HEAD; R1 must not start
        # gating it (any arity). It is is_dangerous=False, mints nothing, and runs
        # free (ALLOW without a token — because it was never gated).
        cmd = _cmd(flags, names)
        assert mgc.is_dangerous_command(cmd) is False
        assert mgc.detect_command_operation_type(cmd) is None
        minted = _mint(cmd, tmp_path)
        assert minted == 0
        assert _execute(cmd, tmp_path) == ALLOW


# ===========================================================================
# NON-VACUITY — three DISTINCT counterfactuals, each neutering ONE of the three
# sites the R1 change spans, proving each is INDEPENDENTLY load-bearing (no dead
# / redundant half). The COMBINED source-only revert of all three hook files is
# performed + documented separately in the TEST HANDOFF (it mutates tracked
# source, so it is not a shipped test); these three in-memory neuters are the
# permanent, hermetic guards.
#
# Contract for each: with the site neutered, the multi-branch faithful click
# FAILS (minted==0 or DENY), while the single-branch control stays correct
# (proving the neuter is surgical, not a blanket break).
# ===========================================================================
class TestNonVacuity:

    def test_extract_half_is_load_bearing(self, tmp_path, monkeypatch):
        # Neuter site 1 (extraction): _extract_branch_delete_set -> None. No branch_set
        # is ever produced, so the multi-branch delete has no target -> no mint -> deny.
        # `_command` (underscore prefix) = intentionally-unused positional: the stub must
        # match the real _extract_branch_delete_set(command) arity, but the neuter ignores it.
        monkeypatch.setattr(mgc, "_extract_branch_delete_set", lambda _command: None)
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]), _cmd(_D, ["aa", "bb"]), tmp_path)
        assert minted == 0, "extraction neutered yet a branch_set token still minted"
        assert rc == DENY
        # Control: single-branch scalar path is independent of _extract_branch_delete_set.
        smint, src = _roundtrip(_cmd(_D, ["solo"]), _cmd(_D, ["solo"]), tmp_path)
        assert smint == 1 and src == ALLOW

    def test_mint_site_is_load_bearing(self, tmp_path, monkeypatch):
        # Neuter site 2 (mint target gate): restore the pre-fix _target_value that omits
        # branch_set (the exact original R1 bug). Extraction still populates branch_set,
        # but the mint cannot see it as a target -> no (op,target) pair -> no mint -> deny.
        def _target_value_without_branch_set(cmd_ctx):
            return (cmd_ctx.get("pr_number")
                    or cmd_ctx.get("branch")
                    or cmd_ctx.get("target_ref")
                    or cmd_ctx.get("mass_target")
                    or cmd_ctx.get("protected_branch"))
        monkeypatch.setattr(merge_guard_post, "_target_value", _target_value_without_branch_set)
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]), _cmd(_D, ["aa", "bb"]), tmp_path)
        assert minted == 0, "mint-site neutered yet a multi-branch token still minted (the gated-but-unmintable bug)"
        assert rc == DENY
        # Control: single-branch uses the `branch` key, still present in _target_value.
        smint, src = _roundtrip(_cmd(_D, ["solo"]), _cmd(_D, ["solo"]), tmp_path)
        assert smint == 1 and src == ALLOW

    def test_read_arm_is_load_bearing(self, tmp_path, monkeypatch):
        # Neuter site 3 (read arm): drop branch_set from the READ side's command context
        # only (post/mint side untouched). The token mints WITH branch_set, but the read
        # arm's branch_set clause can no longer match -> deny.
        _real_ecc = mgc.extract_command_context

        def _ecc_drop_branch_set(command, *a, **k):
            d = dict(_real_ecc(command, *a, **k))
            d.pop("branch_set", None)
            return d
        monkeypatch.setattr(merge_guard_pre, "extract_command_context", _ecc_drop_branch_set)
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]), _cmd(_D, ["aa", "bb"]), tmp_path)
        assert minted == 1, "read-arm neuter must not affect the mint side (token should still mint)"
        assert rc == DENY, "read arm neutered yet a multi-branch token still authorized"
        # Control: single-branch matches on the scalar `branch` key, not branch_set.
        smint, src = _roundtrip(_cmd(_D, ["solo"]), _cmd(_D, ["solo"]), tmp_path)
        assert smint == 1 and src == ALLOW


# ===========================================================================
# REVIEW REMEDIATION (peer-review cycle 1) — the comma-collision under-block
# closed by the #1135 F1 NUL-separator fix, plus the 3 Minor coverage rows the
# review surfaced. These pin the exact witnesses from the review.
# ===========================================================================
class TestCommaNameCollisionClosed:
    """#1135 F1: git ref names PERMIT commas, so the OLD `,`-joined branch_set was
    non-injective — {aa,'bb,cc'} (2 branches) and {aa,bb,cc} (3 branches) both joined
    to `aa,bb,cc`, letting a 2-branch token authorize a 3-branch delete (a cross-set
    UNDER-BLOCK; the review witness). The NUL join (git-forbidden in ref names) makes
    the identity INJECTIVE: the two sets now produce distinct strings, so the token no
    longer cross-authorizes. The over-block direction is untouched (see the self-match)."""

    def test_two_set_and_three_set_no_longer_collide(self):
        two = mgc._extract_branch_delete_set(_cmd(_D, ["aa", "bb,cc"]))       # {aa, 'bb,cc'}
        three = mgc._extract_branch_delete_set(_cmd(_D, ["aa", "bb", "cc"]))  # {aa, bb, cc}
        assert two != three, "comma-named 2-set must not collide with the 3-set (F1)"
        assert two == "aa\x00bb,cc" and three == "aa\x00bb\x00cc"

    def test_two_set_token_does_not_authorize_three_set_delete(self, tmp_path):
        # The review's cross-set under-block witness — now REFUSED.
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb,cc"]), _cmd(_D, ["aa", "bb", "cc"]), tmp_path)
        assert minted == 1, "the 2-branch approval must still MINT (the refuse is a read decision)"
        assert rc == DENY, "a {aa,'bb,cc'} token must NOT authorize deleting {aa,bb,cc} (F1)"

    def test_three_set_token_does_not_authorize_two_set_delete(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb", "cc"]), _cmd(_D, ["aa", "bb,cc"]), tmp_path)
        assert minted == 1
        assert rc == DENY

    def test_comma_named_branch_self_matches_faithful_unaffected(self, tmp_path):
        # Over-block direction untouched: a faithful click deleting a comma-named
        # branch set still mints + authorizes its own byte-identical exec.
        cmd = _cmd(_D, ["aa", "bb,cc"])
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1
        assert rc == ALLOW


class TestBenignContinuationAndCompound:
    """M1 + M2: a multi-branch delete carrying a BENIGN continuation is a single
    destructive leg and mints+authorizes (#1069); a multi-branch delete CHAINED with a
    second destructive op is compound (>=2 destructive legs) and is REFUSED (no mint)."""

    @pytest.mark.parametrize("tail", [" ; echo done", " > /tmp/pact_r1_log", " &"])
    def test_multi_branch_with_benign_continuation_authorizes(self, tail, tmp_path):
        cmd = _cmd(_D, ["aa", "bb"]) + tail
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "faithful multi-branch + benign continuation must mint (#1069)"
        assert rc == ALLOW

    @pytest.mark.parametrize("tail", [" && git push --force", " && rm -rf x"])
    def test_multi_branch_chained_with_destructive_is_compound_refused(self, tail, tmp_path):
        cmd = _cmd(_D, ["aa", "bb"]) + tail
        assert mgc.is_compound_destructive_command(cmd) is True
        minted, rc = _roundtrip(cmd, cmd, tmp_path)
        assert minted == 0, "a compound (>=2 destructive legs) must NOT mint"
        assert rc == DENY


class TestQuotedBranchNames:
    """M3: quoted branch names canonicalize via _strip_surrounding_quotes, so quoting is
    transparent — a faithful click that quotes the names authorizes the unquoted exec
    (and vice-versa), and the canonical identity is quote-insensitive."""

    def _q(self, names):
        return _GB + _D + " " + " ".join("'%s'" % n for n in names)

    def test_quoted_names_canonicalize_quote_insensitively(self):
        assert mgc._extract_branch_delete_set(self._q(["aa", "bb"])) == "aa\x00bb"

    def test_quoted_mint_authorizes_unquoted_exec(self, tmp_path):
        minted, rc = _roundtrip(self._q(["aa", "bb"]), _cmd(_D, ["aa", "bb"]), tmp_path)
        assert minted == 1
        assert rc == ALLOW

    def test_unquoted_mint_authorizes_quoted_reordered_exec(self, tmp_path):
        minted, rc = _roundtrip(_cmd(_D, ["aa", "bb"]), self._q(["bb", "aa"]), tmp_path)
        assert minted == 1
        assert rc == ALLOW
