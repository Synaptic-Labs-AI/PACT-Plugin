"""
Tests for merge_guard_pre._GH_PR_NUMBER_RE — PR-number extraction.

Pins the regex behavior: `_GH_FLAG_TOKENS` restricts BOTH flag-walks (between
`gh` and `pr`, AND between subcommand and PR number) to flag-shaped tokens
only (`-x`, `--long`, optionally `--flag value`). The `(?![\\w-])` negative-
lookahead on the (\\d+) capture rejects suffix matches inside longer
alphanumeric-or-hyphenated tokens.

Each TRUE-GAIN test below cites the exact OLD-vs-NEW behavioral delta — these
are the cases that were broken on main (regex captured a non-PR digit token
from heredoc body / 2>&1 redirect / trailing positional / branch-name suffix)
and now correctly extract the PR number or return None.

Two previously-xfail-strict cases are now FIXED in this file:

  1. "heredoc body containing a fully-formed `gh pr merge <N>` substring":
     `_GH_PR_NUMBER_RE` uses the tight `_GH_FLAG_TOKENS` form for BOTH the
     pre-subcommand AND post-subcommand flag walks (rather than reusing the
     broad `_GH_GLOBAL_FLAGS` for the pre-subcommand walk). This eliminates
     the re-anchor-at-second-occurrence authorization-bypass class.

  2. "branch-name suffix `7352-tests`": Python `\\b` IS a word boundary at
     digit-to-hyphen (word char `2` to non-word char `-`). The earlier spec
     assumed `\\b` would reject this; that assumption was wrong about Python
     regex semantics. The fix replaces the trailing `\\b` with `(?![\\w-])`
     (negative lookahead) — strictly stronger: rejects any continuation that
     is a word char OR a hyphen.

Both `test_heredoc_body_with_embedded_gh_pr_merge` and
`test_branch_name_with_digit_prefix_suffix_match` are now regular passing
tests pinning the fixes; the new `test_authorization_mismatch_attack` test
pins the end-to-end attack shape that the heredoc-side fix prevents.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from merge_guard_pre import _GH_PR_NUMBER_RE  # noqa: E402


def _capture(command: str):
    """Helper: run the regex and return the captured PR number, or None."""
    m = _GH_PR_NUMBER_RE.search(command)
    return m.group(1) if m else None


# =============================================================================
# TRUE GAINS — cases broken on main, fixed by the regex tightening
# =============================================================================
# Each test in this class documents the OLD-vs-NEW behavioral delta. On main,
# the regex used the broad `_GH_GLOBAL_FLAGS` for the post-subcommand walk,
# which greedily consumed tokens past the PR positional and captured the LAST
# digit reachable. After tightening to `_GH_FLAG_TOKENS`, each of these cases
# now captures the real positional PR number.
#
# Selection criterion: a test belongs in TrueGains iff source-only revert of
# the regex tightening commit causes the test to FAIL. Cases that pass on
# both pre-fix and post-fix source belong in NoRegressionCanaries (sibling
# class below) instead.
#
# Counter-test cardinality: source-only revert of the regex tightening
# (`git checkout <pre-fix-sha> -- pact-plugin/hooks/merge_guard_pre.py`)
# causes ALL 4 tests in this class to FAIL with the regex capturing the
# wrong digit. Empirically verified during PR #697 review.

class TestGH_PR_NumberRE_TrueGains:
    """Cases broken on main, fixed by the regex tightening.

    Each test fails on source-only revert of the fix commit. This is the
    discriminating criterion: TRUE GAINS cause failures when the fix is
    reverted. Compare with `TestGH_PR_NumberRE_NoRegressionCanaries`
    (sibling class) where the cases pass on both pre-fix and post-fix
    source — those are no-op protection canaries, not behavioral deltas.
    """

    def test_subject_with_version_digits_captures_pr(self):
        # OLD captured "7352" (last digit in subject string).
        # NEW correctly captures the PR positional "663".
        cmd = 'gh pr merge 663 --squash --subject "v4.1.7 release notes ships 7352 tests"'
        assert _capture(cmd) == "663"

    def test_body_with_version_digits_captures_pr(self):
        # OLD captured "7352" (last digit in body content).
        # NEW correctly captures "663".
        cmd = (
            'gh pr merge 663 --squash --subject foo '
            '--body "v4.1.7 ships 7352 tests passing"'
        )
        assert _capture(cmd) == "663"

    def test_stderr_redirect_captures_pr(self):
        # OLD captured "2" (the "2" of "2>&1" redirect).
        # NEW correctly captures "663".
        cmd = "gh pr merge 663 --squash 2>&1"
        assert _capture(cmd) == "663"

    def test_trailing_positional_text_with_digits(self):
        # OLD captured "7352" from trailing positional text.
        # NEW correctly captures "663".
        cmd = "gh pr merge 663 with 7352 tests passing"
        assert _capture(cmd) == "663"


# =============================================================================
# NO-REGRESSION CANARIES — cases unchanged by the fix; pinned as protection
# =============================================================================
# These tests pass on BOTH pre-fix and post-fix source. They do not demonstrate
# behavioral gain from the fix; instead they pin currently-correct behavior so
# a future regex change cannot silently regress these working cases.
#
# Selection criterion: a test belongs in NoRegressionCanaries iff source-only
# revert of the regex tightening commit leaves the test PASSING. The fix is
# orthogonal to these inputs.
#
# Counter-test cardinality: source-only revert of the regex tightening leaves
# ALL 5 tests in this class PASSING. Empirically verified during PR #697
# review. If a future regression causes any of these to fail under the
# current source, the canary fires loudly.

class TestGH_PR_NumberRE_NoRegressionCanaries:
    """Cases unchanged by the fix — pinned as future-regression tripwires.

    Distinct from `TestGH_PR_NumberRE_TrueGains` (sibling class above)
    where source-only revert of the fix causes failure: these cases pass
    regardless of whether the fix is in place. Their value is forward-
    looking — a future "cleanup" or refactor of the regex that broke any
    of these inputs would be caught by the canary firing.
    """

    def test_body_file_with_versioned_path(self):
        # Empirical probe: OLD captured "663" here (the path's slashes/
        # hyphens are non-word and `\S+\s+` requires whitespace separation,
        # so the broad walk consumes the path and backtracks to the
        # positional). Pinned as a no-op canary so a future regex change
        # cannot regress this case to capturing "7352".
        cmd = "gh pr merge 663 --body-file /tmp/release-notes-v4.1.7-7352.md --squash"
        assert _capture(cmd) == "663"

    def test_simple_squash_unaffected(self):
        # Simple case: no flags between subcommand and PR positional.
        cmd = "gh pr merge 663 --squash"
        assert _capture(cmd) == "663"

    def test_pre_subcommand_flag_unaffected(self):
        # `--admin` between merge subcommand and PR number.
        cmd = "gh pr merge --admin 663 --squash"
        assert _capture(cmd) == "663"

    def test_global_flag_before_subcommand_unaffected(self):
        # `--repo owner/repo` as a gh global flag (pre-subcommand).
        cmd = "gh --repo owner/repo pr merge 663 --squash"
        assert _capture(cmd) == "663"

    def test_close_with_delete_branch_captures_pr(self):
        # `gh pr close` form with `--delete-branch` (sibling subcommand).
        cmd = "gh pr close 663 --delete-branch"
        assert _capture(cmd) == "663"


# =============================================================================
# ACCEPTABLE-NONE — degradation to permissive behavior
# =============================================================================
# When the PR number cannot be unambiguously located, the regex returns None.
# The caller (_token_authorizes_command) treats None as ambiguous and falls
# back to permissive (returns True). This preserves the existing semantic.

class TestGH_PR_NumberRE_AcceptableNone:
    """Cases where the regex correctly returns None (permissive degradation)."""

    def test_no_positional_pr_number(self):
        # gh pr merge --auto has no positional PR number; None is correct.
        cmd = "gh pr merge --auto"
        assert _capture(cmd) is None


# =============================================================================
# AUTHORIZATION-BYPASS FIXES — regression-protection for prior xfail cases
# =============================================================================
# Two cases that were previously pinned as xfail-strict are now passing
# tests after the regex tightening:
#   1. heredoc body containing embedded `gh pr merge <N>` — fixed by tight
#      flag-walks on BOTH sides of `pr <subcmd>`.
#   2. branch-name argument with digit prefix (e.g., `7352-tests`) — fixed
#      by replacing `\b` with `(?![\w-])` (rejects hyphen continuation).

class TestGH_PR_NumberRE_AuthorizationBypassFixed:
    """Fixed authorization-bypass class — pinned as regression-protection.

    These tests pin the regex tightening that closed an authorization-bypass
    where a body string containing `gh pr merge <fake_PR>` could re-anchor
    the regex past the real positional and cause the token-context check
    to compare against the embedded fake PR rather than the real one.
    """

    def test_heredoc_body_with_embedded_gh_pr_merge(self):
        """Body string with embedded `gh pr merge <N>` no longer re-anchors.

        Previously xfail-strict (the regex captured 999 because the broad
        pre-subcommand flag walk consumed past `663 --body "see also gh `
        and re-anchored at the second `pr merge`). After tightening BOTH
        flag-walks to flag-shaped tokens only, the broad walk cannot
        consume past quoted body content, so the first-occurrence anchor
        sticks and the real positional is captured.
        """
        cmd = 'gh pr merge 663 --body "see also gh pr merge 999 example"'
        assert _capture(cmd) == "663"

    def test_authorization_mismatch_attack(self):
        """End-to-end shape of the authorization-bypass attack now blocked.

        Resolves the BLOCKING finding from PR #697 review.

        Attack shape: an attacker (or an honest user with a verbose body)
        constructs `gh pr merge <real> --body "...gh pr merge <fake>..."`.
        Pre-fix, the AskUserQuestion authorization issued for `<real>`
        would be matched against `<fake>` extracted by the regex and
        REJECTED with "Authorization token exists but does not match this
        operation" — masking the real merge. Worse, an attacker could craft
        the body so that the AskUserQuestion-issued token (for `<fake>`,
        a non-existent PR they mention in the body of a different merge)
        authorizes the actual `<real>` merge they intended to bypass.

        Post-fix: the regex always extracts the real positional regardless
        of body content, so the token-context check matches correctly.
        """
        cmd = (
            'gh pr merge 663 --body "$(cat <<EOF\\n'
            'Fixes #999. See related: gh pr merge 999 --admin\\n'
            'EOF\\n)" --squash'
        )
        assert _capture(cmd) == "663"


    def test_branch_name_with_digit_prefix_suffix_match(self):
        """Branch-name argument with digit prefix no longer captures the digit.

        Previously xfail-strict (Python `\\b` IS a word boundary at
        digit-to-hyphen, so `\\b` after `(\\d+)` allowed `7352` to match
        from `7352-tests`). After replacing `\\b` with `(?![\\w-])` the
        trailing-hyphen continuation is rejected and the regex returns
        None, which the caller treats as ambiguous-and-permissive (the
        intended degradation when no clean digit token can be extracted).
        """
        cmd = "gh pr merge 7352-tests --squash"
        assert _capture(cmd) is None


# =============================================================================
# Boundary verification — `\b` semantics that DO work
# =============================================================================
# Document what `\b` correctly rejects, complementing the limitation above.

class TestGH_PR_NumberRE_BoundaryCorrect:
    """The `\\b` boundary correctly rejects alphanumeric suffix matches."""

    def test_no_match_when_followed_by_alpha(self):
        # `\b` works correctly between digit and letter.
        cmd = "gh pr merge 7352abc --squash"
        assert _capture(cmd) is None
