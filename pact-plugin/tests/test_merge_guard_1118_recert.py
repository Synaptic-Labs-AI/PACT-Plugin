"""
Location: pact-plugin/tests/test_merge_guard_1118_recert.py
Summary: Bidirectional base-vs-HEAD-vs-PATCH re-cert matrix for the #1118 QUOTE-SAFE re-model
         (design §7). The shipped carrier 9 (space-only, commit 38f76965) had a quote-unsafe
         value strip whose dangling `"` merged legs and defeated per-leg isolation in BOTH
         directions (SEC-1 over-block / SEC-2 under-block), plus an incomplete cure (RED-1:
         `=`/attached flag spellings) and a shared carrier-8 flaw. The PATCH (commit 6d71a816,
         current HEAD) replaces the non-quote-aware `[^\\s'"]\\S*` matcher with a shared
         quote-BALANCED `_VALUE_TOKEN` consumed via `_strip_flag_values` by BOTH carriers 8 & 9,
         and a form-aware `_selector_flagsep` (space / =-long / =-short / attached-short).

         WHY THIS SUITE EXISTS (design §2.4): the original 44-test matrix was SPACE-FORM-ONLY and
         proved `+N/-0` additive SOURCE, not additive BEHAVIOR — it gave ZERO coverage for the
         embedded-quote leg-merge (SEC-1/SEC-2), the attached-spelling over-block (RED-1), the
         carrier-8 twin, or the leg-count mechanism. That coverage gap is why the regression
         shipped. This suite locks the whole class permanently, asserting a base-vs-HEAD-vs-PATCH
         DIFFERENTIAL (not a bare pass) so each cure row is non-vacuous by construction.

         base  = c5e9b324 (no carrier 9)         — the correct pre-carrier-9 behavior
         HEAD  = 38f76965 (buggy space-only)     — the regressed behavior the PATCH fixes
         PATCH = 6d71a816 (quote-safe re-model)  — the live/shipped module (current HEAD)

         The base/HEAD modules load via the __package__='shared' git-show harness (they are
         permanent merged commits). When history is unavailable (shallow clone), the DIFFERENTIAL
         rows self-SKIP; the absolute PATCH assertions (held battery, cmd-sub, over-match anchor,
         leg-count, structural guards, graphql residual) always run.
Used by: pytest (merge-guard suite).

Dangerous substrings are assembled at runtime (M / GR / PR_MERGE) so this file carries no raw
`gh pr merge` / `git/refs` literal — mirrors the coder + cert files' probe-harness convention.
"""
import subprocess
import sys
import importlib.util
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import pytest  # noqa: E402

import shared.merge_guard_common as PATCH  # noqa: E402  (live module == current HEAD == the PATCH)

# Dangerous substrings assembled at runtime — never a raw literal in the source.
M = "mer" + "ge"                 # merge
GR = "git" + "/refs"             # git/refs
PR_MERGE = f"gh pr {M} 5"        # a real destructive verb, for the cmd-sub carve-out edge

_REPO_ROOT = Path(__file__).resolve().parents[2]  # tests -> pact-plugin -> worktree root
_MGC_PATH = "pact-plugin/hooks/shared/merge_guard_common.py"
_BASE_SHA = "c5e9b324"   # pre-carrier-9 baseline (permanent merged commit)
_HEAD_SHA = "38f76965"   # buggy space-only original carrier 9 (permanent merged commit)


def _load_module_at(sha):
    """Load the merge_guard_common module as it existed at `sha`, or None if unavailable.

    Execs the historical single-file source with __package__='shared' so its sole relative
    import (`from .paths import get_claude_config_dir`) resolves against the LIVE, unchanged
    shared.paths (only merge_guard_common.py changed across these revisions). NON-DISRUPTIVE:
    reads via `git show`, never checks out — HEAD is untouched. Returns None on any failure
    (git missing, shallow clone lacking the commit) so the differential rows self-skip.
    """
    try:
        src = subprocess.check_output(
            ["git", "show", f"{sha}:{_MGC_PATH}"],
            cwd=str(_REPO_ROOT), text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    spec = importlib.util.spec_from_loader(f"shared._mgc_{sha}", loader=None)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]  # spec is never None here
    mod.__package__ = "shared"
    try:
        exec(compile(src, f"<{_MGC_PATH}@{sha}>", "exec"), mod.__dict__)
    except Exception:
        return None
    return mod


# Annotated Any: dynamically exec'd modules have no static type; the attribute accesses
# below (is_dangerous_command / detect_command_operation_type / _split_into_legs) are
# guarded at runtime by _HISTORY_OK (the @requires_history skip), so silence the editor-only
# reportOptionalMemberAccess without a runtime assert.
_BASE: Any = _load_module_at(_BASE_SHA)
_HEAD: Any = _load_module_at(_HEAD_SHA)
_HISTORY_OK = _BASE is not None and _HEAD is not None

# Skip the base-vs-HEAD differential rows when history is unavailable; the absolute PATCH
# assertions still run. The differential IS the non-vacuity proof (each cure row measures a
# real base->PATCH behavior change), so it runs wherever the merged history is present (dev CI).
requires_history = pytest.mark.skipif(
    not _HISTORY_OK,
    reason=f"NON-VACUITY DIFFERENTIAL SKIPPED — base ({_BASE_SHA}) / HEAD ({_HEAD_SHA}) source "
           "unavailable (shallow clone / missing history). Every regression SHAPE is still "
           "guarded by an always-run absolute PATCH assertion; only the base->PATCH "
           "behavior-change PROOF did not run this session.",
)


def _isd_triple(cmd):
    return (_BASE.is_dangerous_command(cmd),
            _HEAD.is_dangerous_command(cmd),
            PATCH.is_dangerous_command(cmd))


def _op_triple(cmd):
    return (_BASE.detect_command_operation_type(cmd),
            _HEAD.detect_command_operation_type(cmd),
            PATCH.detect_command_operation_type(cmd))


# ======================================================================================
# §7.1 SEC-1 — embedded-quote leg-merge OVER-BLOCK cured (base=False, HEAD=True, PATCH=False)
# ======================================================================================
class TestSEC1OverBlockCured:
    SEC1 = {
        "merge_path":   f'gh api repos/o/r/pulls/5/{M} -X GET -q p"q r" && gh api "x/y" -X POST -f a=b',
        "gitrefs_path": f'gh api repos/o/r/{GR}/heads/x -X GET -q p"q r" && gh api "x/y" -X POST -f a=b',
    }

    @pytest.mark.parametrize("cmd", SEC1.values(), ids=list(SEC1.keys()))
    def test_sec1_allows_at_patch(self, cmd):
        # ALWAYS-RUN absolute recurrence guard (the lesson of the arc — guard the SHAPE, not only
        # the differential): the faithful embedded-quote compound is NOT over-blocked at the
        # committed PATCH. Fails if the leg-merge over-block ever returns, regardless of git history.
        assert PATCH.is_dangerous_command(cmd) is False

    @requires_history
    @pytest.mark.parametrize("cmd", SEC1.values(), ids=list(SEC1.keys()))
    def test_sec1_differential(self, cmd):
        # NON-VACUITY layer: base ALLOWED the faithful compound; the leg-merge NEWLY BLOCKED it at
        # HEAD (cardinal sin); PATCH restores allow. The base=False / HEAD=True legs prove the
        # absolute row above measures the REAL cure, not a vacuous pass.
        assert _isd_triple(cmd) == (False, True, False)


# ======================================================================================
# §7.2 SEC-2 — embedded-quote leg-merge UNDER-BLOCK closed (base=True, HEAD=False, PATCH=True)
# ======================================================================================
class TestSEC2UnderBlockClosed:
    SEC2 = {
        "input_dq":  f'gh api repos/o/r/{M}s --input b.json -q x"y z" && gh api "repos/o/r" -X GET',
        "jq_concat": f'gh api repos/o/r/{M}s --input b.json -q .a+" "+.b && gh api "repos/o/r" -X GET',
        "template":  f'gh api repos/o/r/{M}s --input b.json -t {{{{.n}}}}" "{{{{.t}}}} && gh api "repos/o/r" -X GET',
    }

    @pytest.mark.parametrize("cmd", SEC2.values(), ids=list(SEC2.keys()))
    def test_sec2_held_at_patch(self, cmd):
        # ALWAYS-RUN absolute recurrence guard: the real /merges mutation is HELD at the committed
        # PATCH. Fails if the leg-merge under-block (a mutation escaping to allow) ever returns.
        assert PATCH.is_dangerous_command(cmd) is True

    @requires_history
    @pytest.mark.parametrize("cmd", SEC2.values(), ids=list(SEC2.keys()))
    def test_sec2_differential(self, cmd):
        # NON-VACUITY layer: the mutation ESCAPED at HEAD (the merged leg leaked leg-2's -X GET
        # into the .*merge negative lookahead). base held it; PATCH restores the hold.
        assert _isd_triple(cmd) == (True, False, True)


# ======================================================================================
# §7.3 Carrier-8 shared flaw — pre-existing under-block FIXED (base=False, HEAD=False, PATCH=True)
# ======================================================================================
class TestCarrier8TwinFixed:
    CMD = f'gh api repos/o/r/{M}s -f k=x"y z" && gh api "o/r" -X GET'

    def test_carrier8_held_at_patch(self):
        # ALWAYS-RUN absolute recurrence guard: the quote-unsafe carrier-8 body-flag escape is
        # closed — the mutation is HELD at the committed PATCH.
        assert PATCH.is_dangerous_command(self.CMD) is True

    @requires_history
    def test_carrier8_body_flag_quote_unsafe_fixed(self):
        # NON-VACUITY layer: carrier 8's body-flag arm had the SAME non-quote-aware matcher -> a
        # real mutation escaped at BOTH base and HEAD; the shared quote-safe _VALUE_TOKEN closes it
        # at PATCH (base=False, HEAD=False, PATCH=True).
        assert _isd_triple(self.CMD) == (False, False, True)


# ======================================================================================
# §7.4 RED-1 — attached/adjacent selector spellings cured (base=True, HEAD=True, PATCH=False)
# ======================================================================================
class TestRED1AttachmentFormsCured:
    FORMS = {
        "jq_eq_long":        f"gh api graphql -f query=x --jq=.{M}able",
        "template_eq_long":  f"gh api repos/o/r/pulls/1 -f q=x --template=.{M}able",
        "header_eq_long":    f"gh api repos/o/r/pulls/1 -f q=x --header=X-{M}:1",
        "hostname_eq_long":  f"gh api --hostname={M}.example.com -f q=x repos/o/r/pulls/1",
        "preview_eq_long":   f"gh api -f q=x --preview={M}-info repos/o/r/pulls/1",
        "q_eq_short":        f"gh api graphql -f query=x -q=.{M}able",
        "q_attached_short":  f"gh api graphql -f query=x -q.{M}able",
    }

    @pytest.mark.parametrize("cmd", FORMS.values(), ids=list(FORMS.keys()))
    def test_attached_form_now_allows_at_patch(self, cmd):
        # Absolute PATCH assertion (always runs): every attachment spelling of a faithful read
        # is no longer over-blocked.
        assert PATCH.is_dangerous_command(cmd) is False

    @requires_history
    @pytest.mark.parametrize("cmd", FORMS.values(), ids=list(FORMS.keys()))
    def test_attached_form_differential(self, cmd):
        # base AND HEAD both over-blocked the attached spelling (the space-only cure missed it);
        # PATCH cures it. The base=True / HEAD=True legs make the PATCH=False non-vacuous.
        assert _isd_triple(cmd) == (True, True, False)

    def test_held_write_with_attached_selector_stays_held(self):
        # Stripping an attached-short selector value must NOT unblock a real write: the -X DELETE
        # git/refs target lives in the URL positional, never a stripped value.
        cmd = f"gh api -X DELETE repos/o/r/{GR}/heads/x -q.{M}able"
        assert PATCH.is_dangerous_command(cmd) is True
        assert PATCH.detect_command_operation_type(cmd) == "branch-delete"


# ======================================================================================
# §7.5 MINT divergence restored (op: base=None, HEAD=merge, PATCH=None)
# ======================================================================================
class TestMintDivergenceRestored:
    CMD = f'gh api repos/o/r/pulls/5/{M} -X GET -q p"q r" && gh api "x/y" -X POST -f a=b'

    def test_sec1_compound_op_none_at_patch(self):
        # ALWAYS-RUN absolute mint recurrence guard: the SEC-1 compound does NOT mis-bind to a
        # merge op at the committed PATCH (no spurious mint). Fails if the leg-merge mint mis-bind
        # returns — the mint path shares the strip pipeline, so this is a distinct regression axis.
        assert PATCH.detect_command_operation_type(self.CMD) is None

    @requires_history
    def test_sec1_compound_op_classification(self):
        # NON-VACUITY layer: the SEC-1 leg-merge mis-bound the compound to op='merge' at HEAD (a
        # mint mis-bind). base=None, HEAD='merge', PATCH=None.
        assert _op_triple(self.CMD) == (None, "merge", None)


# ======================================================================================
# §7.6 HELD-write regression guard — discriminating negatives @ PATCH (True + op unchanged)
# ======================================================================================
class TestHeldWriteBattery:
    HELD = {
        "pr_merge":        (f"gh pr {M} 1116 --squash", M),
        "merges_body":     (f"gh api repos/o/r/{M}s -f base=main -f head=x", None),
        "merges_input":    (f"gh api repos/o/r/{M}s --input body.json", None),
        "delete_gitrefs":  (f"gh api -X DELETE repos/o/r/{GR}/heads/x", "branch-delete"),
        "put_protection":  ("gh api -X PUT repos/o/r/branches/main/protection -f x=y", "branch-protection"),
        "contents_main":   (f"gh api -X PUT repos/o/r/contents/f.txt -f branch=main --jq '.{M}able'", None),
        "put_pulls_merge": (f"gh api -X PUT repos/o/r/pulls/5/{M} -f merge_method=squash", M),
    }

    @pytest.mark.parametrize("cmd,expected_op", HELD.values(), ids=list(HELD.keys()))
    def test_held_write_stays_held_with_op(self, cmd, expected_op):
        assert PATCH.is_dangerous_command(cmd) is True
        assert PATCH.detect_command_operation_type(cmd) == expected_op


# ======================================================================================
# §7.7 cmd-sub carve-out + over-match anchor negatives @ PATCH
# ======================================================================================
class TestCmdSubCarveOut:
    def test_double_quote_cmdsub_stays_held(self):
        # A double-quoted $(...) executes at runtime -> the value is PRESERVED, the embedded verb
        # stays visible to the danger arms, and the command stays held (merge).
        cmd = f'gh api -H "X: $({PR_MERGE})" repos/o/r/pulls/1'
        assert PATCH.is_dangerous_command(cmd) is True
        assert PATCH.detect_command_operation_type(cmd) == M

    def test_single_quote_cmdsub_allows(self):
        # A single-quoted $(...) is literal shell text (never executes) -> stripped -> allow.
        cmd = f"gh api -H 'X: $({PR_MERGE})' repos/o/r/pulls/1"
        assert PATCH.is_dangerous_command(cmd) is False


class TestOverMatchAnchorNegatives:
    def test_mid_token_dash_q_in_positional_not_stripped(self):
        # `(?<!\S)` anchors the selector flag at a token boundary, so a `-q` embedded inside a
        # positional (`some-q-...`) is NOT read as the --jq short flag: the token survives verbatim
        # in the stripped surface (proving the strip did not mis-consume the following chars).
        cmd = f"gh api repos/o/r/some-q-{M}able -f x=y"
        stripped = PATCH._strip_non_executable_content(cmd)
        assert f"some-q-{M}able" in stripped

    def test_dash_q_inside_quoted_selector_value_allows(self):
        # A `-q` sequence inside a quoted --jq value is part of the (whole-stripped) value, never a
        # second selector flag -> the read allows.
        cmd = f"gh api repos/o/r/pulls/1 -f x=y --jq '.name-q.{M}able'"
        assert PATCH.is_dangerous_command(cmd) is False


# ======================================================================================
# §7.8 Leg-count regression guard — the mechanism-level signature of the leg-merge bug
# ======================================================================================
class TestLegCountRegressionGuard:
    SEC1 = f'gh api repos/o/r/pulls/5/{M} -X GET -q p"q r" && gh api "x/y" -X POST -f a=b'

    def test_patch_splits_two_legs(self):
        # Absolute PATCH mechanism guard: the dangling-quote compound splits into 2 legs (the
        # quote-safe strip never merges them). This is the mechanism the regression corrupted.
        assert len(PATCH._split_into_legs(self.SEC1)) == 2

    @requires_history
    def test_leg_count_differential(self):
        # The regression signature: base=2 legs (correct), HEAD=1 leg (merged — the bug), PATCH=2
        # (restored). Pins that the mechanism cannot silently regress to the merged state.
        assert len(_BASE._split_into_legs(self.SEC1)) == 2
        assert len(_HEAD._split_into_legs(self.SEC1)) == 1
        assert len(PATCH._split_into_legs(self.SEC1)) == 2


# ======================================================================================
# §7.9 Unbalanced-quote-within-token edge — base-equivalent (introduces no new over/under-block)
# ======================================================================================
class TestUnbalancedTokenBaseEquivalence:
    UNBAL = f'gh api repos/o/r/pulls/1 -f q=x -q a"b'  # `a"b` is itself a shell syntax error

    def test_patch_equals_base_on_unbalanced_token(self):
        # The consumer matches `a`, cannot consume the unterminated `"b`, and leaves the
        # pre-existing lone `"` — base-equivalent (such input already fails _shell_tokenize /
        # fails-toward-unmasked at the same offset). Non-faithful; never a new over/under-block.
        assert PATCH.is_dangerous_command(self.UNBAL) is False

    @requires_history
    def test_unbalanced_token_differential_is_inert(self):
        assert _BASE.is_dangerous_command(self.UNBAL) == PATCH.is_dangerous_command(self.UNBAL)


# ======================================================================================
# §7.10 Structural group-index guards — pin the shared helper's capturing-group contract
# ======================================================================================
class TestStructuralGroupIndexGuards:
    def test_value_token_is_non_capturing(self):
        # The shared _VALUE_TOKEN primitive MUST stay non-capturing (0 groups): keep_fn reads
        # m.group(1) = the flag+separator, and the sq arm backrefs \1 = the flag+separator. A stray
        # capturing group in _VALUE_TOKEN would shift those indices and silently mis-strip.
        import re
        assert re.compile(PATCH._VALUE_TOKEN).groups == 0

    def test_strip_flag_values_group1_contract(self):
        # Behavioral pin of the group-index contract via the importable shared helper: with a
        # ONE-group flag_sep_regex, both the dq/unquoted keep_fn (m.group(1)) and the sq arm (\1)
        # must resolve to the flag+separator. A group-count drift would raise or mis-render here.
        def _keep(m):
            return m.group(1) + "'STRIPPED'"
        # single-quoted value -> sq arm's \1 backref
        assert PATCH._strip_flag_values("-q '.secret'", r"(-q\s+)", _keep) == "-q 'STRIPPED'"
        # unquoted VALUE-TOKEN -> keep_fn's m.group(1)
        assert PATCH._strip_flag_values("-q .secret", r"(-q\s+)", _keep) == "-q 'STRIPPED'"

    def test_sq_flag_token_round_trip_through_real_pipeline(self):
        # Through the REAL _strip_non_executable_content (exercising the function-local
        # _selector_flagsep): a held write's -q selector keeps its flag+separator and its value
        # becomes the balanced 'STRIPPED' placeholder (no dangling quote).
        cmd = f"gh api -X DELETE repos/o/r/{GR}/heads/x -q '.{M}able'"
        stripped = PATCH._strip_non_executable_content(cmd)
        assert "-q " in stripped
        assert "'STRIPPED'" in stripped
        assert f".{M}able" not in stripped


# ======================================================================================
# YELLOW-2 (from the Task #21 review) — graphql-mutation residual is a DOCUMENTED, UNCHANGED gap
# ======================================================================================
class TestGraphqlMutationResidualDocumented:
    def test_graphql_mutation_under_block_residual_unchanged(self):
        # The graphql-mutation gap (a `-f query='mutation{...}'` bypasses REST-path matching) is a
        # SEPARATE tracked residual, deliberately NOT closed by #1118. Pin it as is_dangerous=False
        # at PATCH so the documented residual cannot silently drift (over- OR under-block) unnoticed.
        cmd = f"gh api graphql -f query='mutation{{ {M}PullRequest(input:{{}}) {{ number }} }}'"
        assert PATCH.is_dangerous_command(cmd) is False

    @requires_history
    def test_graphql_mutation_residual_is_preexisting(self):
        # Unchanged across base and PATCH — confirms #1118 neither introduced nor closed it.
        cmd = f"gh api graphql -f query='mutation{{ {M}PullRequest(input:{{}}) {{ number }} }}'"
        assert _BASE.is_dangerous_command(cmd) == PATCH.is_dangerous_command(cmd) is False
