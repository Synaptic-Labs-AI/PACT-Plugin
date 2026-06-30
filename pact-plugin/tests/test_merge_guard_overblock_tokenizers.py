"""Merge-guard over-block tokenizer batch — comprehensive + adversarial TEST surfaces.

Proves the three committed source fixes WORK and introduce ZERO under-block. The
SACROSANCT honest-mistake threat model governs: over-block (refusing a faithful
click) is fails-safe but a bug; UNDER-block (letting an honest destructive op
through ungated, or authorizing the WRONG target) is NEVER acceptable.

Fixes under test (committed; this suite pins them):
  • #1043 — _extract_force_push_target_ref / _extract_branch_name truncate at the
    first benign terminator on the quote-masked view BEFORE counting positionals
    (_executable_prefix / _BENIGN_TERMINATOR_RE), so a faithful single force-push /
    branch-delete + benign continuation re-derives its target instead of
    over-blocking. The redirect FILENAME is structurally outside the positional
    window, so it can never become the target.
  • #1069 — the read seam derives (op, target, bound_flags) from the SINGLE
    is_dangerous leg (_single_destructive_leg) instead of the whole command. This
    closes (a) flag pollution from a benign neighbor leg (over-block) and (b) the
    latent target cross-contamination UNDER-block (_extract_pr_number's
    first-match-anywhere scan bleeding a reversible close-leg's PR number into a
    merge-leg's target).
  • #1059 — the mint-side flag scan space-replaces markdown command-wrapper
    delimiters (_strip_command_wrapper) so mint bound_flags == read bound_flags for
    a backtick-wrapped value-flag (shipped v4.4.46; pinned here).

NON-VACUITY (per-pin mutation map; the mechanism DIFFERS per pin — a single
"revert Fix B" control does NOT discriminate every pin). Evidence recorded in the
TEST-phase HANDOFF; mutations exercised in-process against the SUT:
  • Latent target under-block (TestFixBLatentUnderBlockClosure): monkeypatch
    _single_destructive_leg → whole `command` (faithfully reproduces pre-#1069,
    since the seam is `_single_destructive_leg(command) or command`) → the mismatch
    pin flips REFUSE→AUTHORIZE (RED). DISCRIMINATOR via Fix-B revert.
  • Flag-scan over-block fix (TestFixBFlagScanNarrowing, the --squash AUTHORIZE
    case): same whole-command mutation → AUTHORIZE→REFUSE (RED). DISCRIMINATOR via
    Fix-B revert.
  • Flag-scan under-block GUARD (the --admin REFUSE case): the whole-command
    mutation binds MORE flags → stays REFUSE (NOT a discriminator). Its non-vacuity
    needs a FLAG-DROPPING mutation (strip dash-tokens from the isolated leg) →
    REFUSE→AUTHORIZE (RED). DISCRIMINATOR via flag-drop mutation.
  • Fix A extractor pins: monkeypatch _executable_prefix → identity (no
    truncation) re-opens the positional over-block / mis-count → the target pins
    flip (RED).
  • Force-push/branch-delete wrong-target (TestForcePushBranchDeleteDefensiveWrongTarget):
    DEFENSIVE — the cross-contamination variant is non-reproducing as an under-block
    on this code (exact-positional-count extractors fail-safe to over-block, unlike
    _extract_pr_number). Non-vacuity is anchored by the matching-target companion
    (the SAME command AUTHORIZES the correct target), proving the refusal is
    target-discrimination, not a trivial no-token refuse.

Auth-path convention: check_merge_authorization returns a string (REFUSE / deny
reason) or None (AUTHORIZE). The pre-hook envelope exit code is 2 (DENY) / 0
(AUTHORIZE). write_token(context, token_dir=tmp) mints; both are the REAL seam
(token_dir is a production param, not a stubbed mock).
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from merge_guard_post import _strip_command_wrapper, main as _post_main, write_token
from merge_guard_pre import check_merge_authorization, main as _pre_main
from shared.merge_guard_common import (
    _executable_prefix,
    _extract_branch_name,
    _extract_force_push_target_ref,
    _single_destructive_leg,
    extract_command_context,
    is_compound_destructive_command,
    is_dangerous_command,
)

# The canonical benign-continuation family Fix A / Fix B must tolerate (same 13
# forms as TestBenignContinuationGuarantee._BENIGN_CONTINUATIONS): pipe-to-viewer,
# output / fd redirect, background, and benign chain. None is a second destructive
# leg; none may inflate a positional count or pollute a bound-flag set.
_BENIGN_CONTINUATIONS = [
    "| tail", "| head", "| grep merged", "| cat", "| wc -l",
    "| tee /tmp/out", "| less",
    "> out.log", "2>&1", "&> out.log",
    "&", "&& echo done", "; echo done",
]

# The latent under-block exec (design §10.2): a reversible `gh pr close 1058`
# (NOT dangerous) chained with a real merge of a DIFFERENT PR. Pre-#1069 the
# whole-command first-match scan bled pr_number=1058 (from the close leg) into the
# merge context, so a token approved for merge 1058 authorized merging 999. Exactly
# one leg is dangerous (the merge), so is_compound does NOT catch it.
_LATENT_UNDERBLOCK_EXEC = "gh pr close 1058\ngh pr merge 999 --squash"


# ───────────────────────────── envelope helpers (real seam, no mocks) ─────────
def _invoke_post(question: str, tmp, answer: str, options: list) -> int:
    """Drive merge_guard_post.main() in-process over a real stdin envelope with
    TOKEN_DIR redirected to tmp. Returns the process exit code (0 = minted)."""
    envelope = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": question, "options": options}]},
        "tool_response": {"answers": {question: answer}},
        "session_id": "test-overblock-tokenizers",
    })
    with patch("merge_guard_post.TOKEN_DIR", tmp), \
         patch("sys.stdin", io.StringIO(envelope)):
        with pytest.raises(SystemExit) as exc:
            _post_main()
    return exc.value.code


def _invoke_pre(command: str, tmp) -> tuple[int, str]:
    """Drive merge_guard_pre.main() in-process. Returns (exit_code, stdout).
    exit 2 = DENY, exit 0 = AUTHORIZE."""
    envelope = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "test-overblock-tokenizers",
    })
    out = io.StringIO()
    with patch("merge_guard_pre.TOKEN_DIR", tmp), \
         patch("sys.stdin", io.StringIO(envelope)), \
         patch("sys.stdout", out):
        with pytest.raises(SystemExit) as exc:
            _pre_main()
    return exc.value.code, out.getvalue()


# ═══════════════════════════════ #1059 — flag-scan wrapper strip ═════════════
class TestStripCommandWrapper1059:
    """#1059 unit: _strip_command_wrapper space-replaces the markdown command
    wrapper delimiters (backtick + curly quotes) so the mint's privileged-flag
    scan matches the read side's on the bare command. Space-REPLACE (never
    delete) is load-bearing — it preserves token boundaries so a wrapper char
    BETWEEN a value and a flag reveals the flag instead of gluing it."""

    @pytest.mark.parametrize("raw,expected", [
        ("`gh pr merge 5 --repo o/x`", " gh pr merge 5 --repo o/x "),  # backtick -> space
        ("--repo owner/x`", "--repo owner/x "),                        # trailing backtick
        ("‘a’“b”", " a  b "),                      # curly single + double quotes
        ("plain --admin", "plain --admin"),                            # no wrapper -> untouched
        ("'straight' --admin", "'straight' --admin"),                  # straight quotes preserved
    ])
    def test_wrapper_delimiters_become_spaces(self, raw, expected):
        assert _strip_command_wrapper(raw) == expected

    def test_space_replace_preserves_token_boundary_reveals_flag(self):
        """The crux: a backtick BETWEEN a value and a flag must become a SPACE,
        not be deleted (deletion would glue `owner/x--admin` and HIDE --admin from
        the flag scan — a silent privileged-flag drop = under-block surface)."""
        out = _strip_command_wrapper("owner/x`--admin")
        assert out == "owner/x --admin"
        assert "--admin" in out.split()  # surfaces as its own token


class TestMintReadFlagParity1059:
    """#1059 end-to-end: a faithful click whose command carries a backtick-wrapped
    value-flag (`--repo owner/x`) mints and AUTHORIZES, because the mint's
    flag-scan surface (stripped) yields the SAME bound_flags as the read side's on
    the bare command. Pins the v4.4.46 over-block fix."""

    _BARE = "gh pr merge 5 --repo owner/x"
    _OPTION_TEXT = "Run `gh pr merge 5 --repo owner/x`"

    def test_mint_bound_flags_equal_read_bound_flags(self):
        read_ctx = extract_command_context(self._BARE)
        mint_ctx = extract_command_context(
            self._BARE, flag_scan_text=_strip_command_wrapper(self._OPTION_TEXT)
        )
        assert mint_ctx["bound_flags"] == read_ctx["bound_flags"] == ["--repo=owner/x"]

    def test_strip_is_load_bearing_nonvacuity(self):
        """Without the wrapper strip the trailing backtick is captured INTO the
        flag value (`owner/x\\``), desyncing mint from read — the exact #1059
        over-block. Proves the parity assertion above is coupled to the strip."""
        read_ctx = extract_command_context(self._BARE)
        unstripped = extract_command_context(self._BARE, flag_scan_text=self._OPTION_TEXT)
        assert unstripped["bound_flags"] != read_ctx["bound_flags"]

    def test_backtick_value_flag_click_authorizes_e2e(self, tmp_path):
        """Real seam: a token minted with the stripped flag-scan surface authorizes
        the faithful bare command (mint==read parity → AUTHORIZE)."""
        mint_ctx = extract_command_context(
            self._BARE, flag_scan_text=_strip_command_wrapper(self._OPTION_TEXT)
        )
        assert write_token(dict(mint_ctx), token_dir=tmp_path) is not None
        assert check_merge_authorization(self._BARE, token_dir=tmp_path) is None  # AUTHORIZE


# ═══════════════════════ #1043 — Fix A extractor robustness ══════════════════
class TestFixAForcePushContinuationTarget:
    """#1043: _extract_force_push_target_ref re-derives the SAME target across the
    full benign-continuation family. Direct extractor-level pin (finer than the
    end-to-end authorize): the truncation happens at the extractor, so mint and
    read inherit it identically.

    Non-vacuity: monkeypatch _executable_prefix → identity (no truncation) and the
    continuation tokens inflate the positional count → None (the pre-#1043
    over-block). Recorded in HANDOFF."""

    @pytest.mark.parametrize("cont", _BENIGN_CONTINUATIONS)
    def test_target_rederived_across_continuation(self, cont):
        assert _extract_force_push_target_ref(f"git push --force origin main {cont}") == "main"


class TestFixABranchDeleteContinuationTarget:
    """#1043: _extract_branch_name re-derives the SAME branch across the full
    benign-continuation family. Direct extractor-level pin."""

    @pytest.mark.parametrize("cont", _BENIGN_CONTINUATIONS)
    def test_name_rederived_across_continuation(self, cont):
        assert _extract_branch_name(f"git branch -D victim {cont}") == "victim"


class TestFixAUnderBlockNegatives:
    """#1043 §10.3: the under-block negatives — Fix A may ONLY turn an over-block
    (None) into a correct target for a clearly-benign continuation, or stay None.
    It must NEVER mint a WRONG target that could match a token. Each case proves a
    distinct guard."""

    def test_redirect_filename_never_becomes_forcepush_target(self):
        # `... feature > main`: the redirect filename `main` is structurally OUTSIDE
        # the positional window → target is `feature`, never `main`.
        assert _extract_force_push_target_ref("git push --force origin feature > main") == "feature"

    def test_redirect_target_is_the_real_ref_not_the_filename(self):
        # Symmetric control: when the ref IS main, a redirect to a same-named file
        # still yields `main` from the positional window (not the redirect path).
        assert _extract_force_push_target_ref("git push --force origin main > /tmp/main") == "main"

    def test_genuine_multiref_forcepush_refuses(self):
        # A real extra positional (not a continuation) keeps the count off 2 → None.
        assert _extract_force_push_target_ref("git push --force origin main feature") is None

    def test_genuine_extra_positional_before_redirect_refuses(self):
        # The truncation strips the redirect, NOT a real extra positional → 3 → None.
        assert _extract_force_push_target_ref("git push --force origin main extra > log") is None

    def test_quoted_metachar_preserved_in_refspec(self):
        # A quoted `>` is masked → not a terminator → the refspec value is preserved.
        assert _extract_force_push_target_ref('git push --force origin "weird>name"') == "weird>name"

    def test_unbalanced_quote_abstains_forcepush(self):
        # Adversarial quote-elision (out of scope): _shell_tokenize fails →
        # _executable_prefix None → extractor None → the existing safe over-block.
        assert _executable_prefix('git push --force origin "main') is None
        assert _extract_force_push_target_ref('git push --force origin "main') is None

    def test_multitarget_branch_delete_refuses(self):
        # >1 positional branch name → None (the #1032 multi-target under-block guard).
        assert _extract_branch_name("git branch -D a b") is None

    def test_branch_delete_redirect_filename_never_becomes_name(self):
        assert _extract_branch_name("git branch -D victim > feature") == "victim"

    def test_branch_delete_extra_positional_before_redirect_refuses(self):
        assert _extract_branch_name("git branch -D feature extra > log") is None

    def test_branch_delete_unbalanced_quote_abstains(self):
        assert _extract_branch_name('git branch -D "victim') is None


# ═══════════════════ #1069 — latent target cross-contamination ═══════════════
class TestFixBLatentUnderBlockClosure:
    """#1069 §10.2 — the SACROSANCT centerpiece. A token approved for `gh pr merge
    1058` must NOT authorize executing `gh pr close 1058 ; gh pr merge 999` (which
    actually merges 999). The reversible close-leg is NOT dangerous, so exactly ONE
    leg is dangerous (the merge 999) → is_compound does NOT catch it; the read seam
    isolates that leg → target 999 ≠ token 1058 → REFUSE.

    Non-vacuity (Fix-B revert discriminator): monkeypatch _single_destructive_leg →
    whole command re-opens the bleed (whole ctx pr_number=1058 from the close leg)
    → the mismatch pin flips REFUSE→AUTHORIZE. The token{merge,999} control proves
    the REFUSE is the target-mismatch path, not a trivial no-match refuse."""

    def test_discriminator_is_target_mismatch_not_compound(self):
        # Pin the premise: the exec is dangerous (a real merge) but NOT compound
        # (one destructive leg) — so a refusal can only be the isolated-leg target
        # mismatch, never the compound gate.
        assert is_dangerous_command(_LATENT_UNDERBLOCK_EXEC) is True
        assert is_compound_destructive_command(_LATENT_UNDERBLOCK_EXEC) is False
        assert _single_destructive_leg(_LATENT_UNDERBLOCK_EXEC) == "gh pr merge 999 --squash"

    def test_token_for_wrong_pr_refuses_function_seam(self, tmp_path):
        assert write_token(
            {"operation_type": "merge", "pr_number": "1058"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(
            _LATENT_UNDERBLOCK_EXEC, token_dir=tmp_path
        ) is not None  # REFUSE

    def test_token_for_real_merged_pr_authorizes_control(self, tmp_path):
        # Non-vacuity control: a token for the PR actually merged (999) AUTHORIZES
        # the SAME exec — so the mismatch refusal above is target-discrimination.
        assert write_token(
            {"operation_type": "merge", "pr_number": "999"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(
            _LATENT_UNDERBLOCK_EXEC, token_dir=tmp_path
        ) is None  # AUTHORIZE

    def test_token_for_wrong_pr_denies_full_envelope(self, tmp_path):
        # Real executed auth path: mint merge-1058 via the post hook, then run the
        # cross-contaminating exec through the pre hook → DENY (exit 2).
        post_code = _invoke_post(
            "Merge PR 1058?", tmp_path, answer="Yes, merge",
            options=[
                {"label": "Yes, merge", "description": "Run `gh pr merge 1058`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0
        token = json.loads(next(tmp_path.glob("merge-authorized-*")).read_text())
        assert token["context"]["pr_number"] == "1058"
        pre_code, _ = _invoke_pre(_LATENT_UNDERBLOCK_EXEC, tmp_path)
        assert pre_code == 2  # DENY (would be 0 under the pre-#1069 under-block)

    def test_token_for_real_merged_pr_authorizes_full_envelope(self, tmp_path):
        post_code = _invoke_post(
            "Merge PR 999?", tmp_path, answer="Yes, merge",
            options=[
                {"label": "Yes, merge", "description": "Run `gh pr merge 999`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0
        pre_code, _ = _invoke_pre(_LATENT_UNDERBLOCK_EXEC, tmp_path)
        assert pre_code == 0  # AUTHORIZE (the real merged PR)


# ═══════════════════ #1069 — flag-scan narrowing (BLIND-VERIFICATION AIM) ════
class TestFixBFlagScanNarrowing:
    """#1069 §10.1 — the ONE place fix-direction (bind fewer flags) coincides with
    under-block-direction (drop a flag). Two distinct properties:

      OVER-BLOCK FIX (a benign neighbor's flag must NOT pollute): a non-privileged
        op + a neighbor carrying `--repo` AUTHORIZES a no-flag token. (Fix-B revert
        discriminator: whole-command binds the neighbor's --repo → REFUSE.)

      UNDER-BLOCK GUARD (the op's OWN privileged flag must STAY bound): `--admin`
        on the destructive leg REFUSES a no-flag token. NOT a Fix-B-revert
        discriminator (whole-command binds MORE → still REFUSE); its non-vacuity is
        a FLAG-DROPPING mutation (strip dash-tokens from the isolated leg →
        AUTHORIZE). The exactly-one-dangerous-leg invariant (is_compound REFUSES
        >=2 upstream) means the only reachable narrowing is dropping a NEIGHBOR's
        flag — the op's own flag is structurally on its leg."""

    def test_benign_neighbor_flag_does_not_overblock(self, tmp_path):
        # `--squash` is non-privileged; the neighbor's `--repo` is on a DIFFERENT
        # leg → dropped → no-flag token AUTHORIZES (the over-block fix).
        cmd = "gh pr merge 5 --squash ; gh pr view 5 --repo o/x"
        assert write_token(
            {"operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None  # AUTHORIZE

    def test_privileged_flag_on_destructive_leg_stays_bound(self, tmp_path):
        # `--admin` is on the destructive merge leg → bound → a no-flag token is
        # REFUSED (never-escalate preserved). The under-block guard.
        cmd = "gh pr merge 5 --admin ; gh pr view 5 --repo o/x"
        assert write_token(
            {"operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None  # REFUSE

    @pytest.mark.parametrize("cont", _BENIGN_CONTINUATIONS)
    def test_admin_stays_bound_across_all_continuations(self, cont, tmp_path):
        # Defensive matrix: NO benign continuation can strip `--admin` off the
        # destructive leg. A no-flag token is REFUSED for every form.
        cmd = f"gh pr merge 5 --admin {cont}"
        assert is_compound_destructive_command(cmd) is False
        assert write_token(
            {"operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None  # REFUSE

    def test_exec_extra_privileged_flag_on_leg_still_refuses(self, tmp_path):
        # The #1042 bind is byte-unchanged: a token binding --admin must still
        # REFUSE an exec adding a SECOND merge-privileged flag (--match-head-commit)
        # on the ISOLATED destructive leg — leg-isolation does not weaken the bind.
        assert write_token(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            token_dir=tmp_path,
        ) is not None
        cmd = "gh pr merge 5 --admin --match-head-commit deadbeef ; gh pr view 5"
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None  # REFUSE


# ═══════════ force-push / branch-delete DEFENSIVE wrong-target pin ═══════════
class TestForcePushBranchDeleteDefensiveWrongTarget:
    """The force-push / branch-delete target cross-contamination variant is
    NON-reproducing as an under-block on this code (the exact-positional-count
    extractors fail-safe to over-block, unlike _extract_pr_number's
    first-match-anywhere). Pinned DEFENSIVELY so a future regression cannot
    silently open it.

    Non-vacuity is anchored by the matching-target companion: the SAME command
    AUTHORIZES the correct target — so the wrong-target refusal is
    target-discrimination, not a trivial no-token refuse."""

    _FP_EXEC = 'git push --force origin feature ; echo "push main"'
    _BD_EXEC = "git branch -D feature ; echo done"

    def test_forcepush_wrong_target_refuses(self, tmp_path):
        assert write_token(
            {"operation_type": "force-push", "target_ref": "main"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(self._FP_EXEC, token_dir=tmp_path) is not None  # REFUSE

    def test_forcepush_matching_target_authorizes_companion(self, tmp_path):
        assert write_token(
            {"operation_type": "force-push", "target_ref": "feature"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(self._FP_EXEC, token_dir=tmp_path) is None  # AUTHORIZE

    def test_branch_delete_wrong_target_refuses(self, tmp_path):
        assert write_token(
            {"operation_type": "branch-delete", "branch": "main"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(self._BD_EXEC, token_dir=tmp_path) is not None  # REFUSE

    def test_branch_delete_matching_target_authorizes_companion(self, tmp_path):
        assert write_token(
            {"operation_type": "branch-delete", "branch": "feature"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(self._BD_EXEC, token_dir=tmp_path) is None  # AUTHORIZE


# ═══════════════════════ mint == read parity canary (§7) ═════════════════════
class TestMintReadParityCanary:
    """The cross-cutting invariant: for a faithful destructive op + benign neighbor
    carrying a flag, the READ side's isolated-leg context EQUALS the MINT side's
    single-command context (op, target, AND bound_flags). The #1069 read isolation
    brings the read into per-leg parity with the mint (which already isolates per
    region via locate_command_regions)."""

    def test_read_isolated_leg_context_equals_minted_command_context(self):
        executed = "gh pr merge 5 --admin ; gh pr view 5 --repo owner/x"
        minted_command = "gh pr merge 5 --admin"
        read_ctx = extract_command_context(_single_destructive_leg(executed))
        mint_ctx = extract_command_context(minted_command)
        assert read_ctx == mint_ctx
        assert read_ctx["bound_flags"] == ["--admin"]  # the neighbor's --repo is NOT bound

    def test_canary_force_push_with_redirect_parity(self):
        # A faithful force-push + redirect: read isolates the leg, both arms derive
        # target_ref=main with no bound flags.
        executed = "git push --force origin main > out.log"
        read_ctx = extract_command_context(_single_destructive_leg(executed))
        mint_ctx = extract_command_context("git push --force origin main")
        assert read_ctx == mint_ctx
        assert read_ctx.get("target_ref") == "main"


# ═══════════════ #1069 — CLOSE op-class leg-isolation (review gap) ════════════
class TestFixBCloseOpLegIsolation:
    """Close op-class coverage for the #1069 leg-isolation, symmetric to the merge
    pins above (which centered merge). Close is the higher-stakes op: its danger
    trigger ``--delete-branch`` is IRREVERSIBLE, so an isolation regression here
    would mis-delete a branch. The closing mechanism (_single_destructive_leg +
    _extract_pr_number) is op-AGNOSTIC and already regression-guarded by the merge
    pins; these make the close op explicit and guard a future op-specific divergence.

    The CLOSE latent exec: a bare reversible ``gh pr close 1058`` (NOT dangerous)
    chained with an IRREVERSIBLE ``gh pr close 999 --delete-branch``. Exactly one
    leg is dangerous (the delete-close), so is_compound does NOT catch it; pre-#1069
    the whole-command first-match scan bled pr_number=1058 (from the reversible
    close leg), so a token approved for closing+deleting 1058's branch would
    AUTHORIZE deleting 999's branch."""

    _CLOSE_LATENT_EXEC = "gh pr close 1058\ngh pr close 999 --delete-branch"

    def test_discriminator_is_target_mismatch_not_compound(self):
        assert is_dangerous_command(self._CLOSE_LATENT_EXEC) is True
        assert is_compound_destructive_command(self._CLOSE_LATENT_EXEC) is False
        assert _single_destructive_leg(self._CLOSE_LATENT_EXEC) == (
            "gh pr close 999 --delete-branch"
        )

    def test_close_token_for_wrong_pr_refuses(self, tmp_path):
        # UNDER-BLOCK pin: token approved to delete 1058's branch must NOT authorize
        # deleting 999's branch. Non-vacuity (Fix-B revert): _single_destructive_leg
        # -> whole re-opens the bleed (whole pr_number=1058) -> AUTHORIZE = RED.
        assert write_token(
            {"operation_type": "close", "pr_number": "1058",
             "bound_flags": ["--delete-branch"]},
            token_dir=tmp_path,
        ) is not None
        assert check_merge_authorization(
            self._CLOSE_LATENT_EXEC, token_dir=tmp_path
        ) is not None  # REFUSE

    def test_close_token_for_real_deleted_pr_authorizes_control(self, tmp_path):
        # Non-vacuity control: a token for the PR actually delete-closed (999)
        # AUTHORIZES the same exec -> the mismatch refusal is target-discrimination.
        assert write_token(
            {"operation_type": "close", "pr_number": "999",
             "bound_flags": ["--delete-branch"]},
            token_dir=tmp_path,
        ) is not None
        assert check_merge_authorization(
            self._CLOSE_LATENT_EXEC, token_dir=tmp_path
        ) is None  # AUTHORIZE

    def test_close_benign_neighbor_flag_does_not_overblock(self, tmp_path):
        # OVER-BLOCK fix (close flag-pollution): a benign neighbor's --repo on a
        # different leg is dropped from the close leg's bound_flags, so the faithful
        # delete-close AUTHORIZES with its matching token.
        cmd = "gh pr close 5 --delete-branch ; gh pr view 5 --repo o/x"
        assert write_token(
            {"operation_type": "close", "pr_number": "5",
             "bound_flags": ["--delete-branch"]},
            token_dir=tmp_path,
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None  # AUTHORIZE

    def test_close_delete_branch_flag_on_leg_stays_bound(self, tmp_path):
        # UNDER-BLOCK guard (close analog of merge's --admin-stays-bound): a token
        # for a bare reversible close must NOT authorize an IRREVERSIBLE delete-close
        # — --delete-branch on the close leg stays bound, so the no-delete-branch
        # token is REFUSED (the bare->delete-variant escalation is closed). Non-
        # vacuity is the flag-drop mutation (strip dash-tokens -> --delete-branch
        # dropped -> AUTHORIZE = RED), NOT a whole-command revert (which binds MORE).
        cmd = "gh pr close 5 --delete-branch ; gh pr view 5"
        assert write_token(
            {"operation_type": "close", "pr_number": "5"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None  # REFUSE

    def test_close_faithful_delete_close_authorizes_companion(self, tmp_path):
        # Companion to the guard: the FAITHFUL token (binding --delete-branch) for the
        # same exec AUTHORIZES — so the refusal above is the missing-flag escalation,
        # not a trivial no-match.
        cmd = "gh pr close 5 --delete-branch ; gh pr view 5"
        assert write_token(
            {"operation_type": "close", "pr_number": "5",
             "bound_flags": ["--delete-branch"]},
            token_dir=tmp_path,
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None  # AUTHORIZE


# ════════════════ destructive-leg ORDER independence (review gap) ════════════
class TestDestructiveLegOrderIndependence:
    """_single_destructive_leg isolates the unique dangerous leg regardless of its
    POSITION among benign neighbors. The pins above place the destructive leg first;
    these place it SECOND (and amid multiple neighbors), so a future change keying on
    leg ORDER rather than leg DANGER is caught."""

    def test_destructive_leg_second_admin_stays_bound(self, tmp_path):
        # Destructive leg SECOND: --admin on the (second) merge leg still binds -> a
        # no-flag token is REFUSED, exactly as when the merge leg is first.
        cmd = "gh pr view 5 ; gh pr merge 5 --admin"
        assert _single_destructive_leg(cmd) == "gh pr merge 5 --admin"
        assert write_token(
            {"operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None  # REFUSE

    def test_destructive_leg_amid_multiple_neighbors_target_isolated(self, tmp_path):
        # One dangerous merge leg amid two benign neighbors: the target (999) is the
        # merge leg's, so a token for a DIFFERENT pr (1) is REFUSED (no cross-leg bleed).
        cmd = "gh pr view 5 ; gh pr merge 999 ; gh pr view 6"
        assert _single_destructive_leg(cmd) == "gh pr merge 999"
        assert write_token(
            {"operation_type": "merge", "pr_number": "1"}, token_dir=tmp_path
        ) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None  # REFUSE
