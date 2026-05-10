"""
Tests for merge_guard_pre._GH_PR_NUMBER_RE — PR-number extraction.

Pins the regex behavior: `_GH_FLAG_TOKENS` restricts BOTH flag-walks (between
`gh` and `pr`, AND between subcommand and PR number) to flag-shaped tokens
only (`-x`, `--long`, optionally `--flag value`). The `\\b` boundary on the
(\\d+) capture rejects suffix matches inside longer alphanumeric tokens.

Each TRUE-GAIN test below cites the exact OLD-vs-NEW behavioral delta — these
are the cases that were broken on main (regex captured a non-PR digit token
from heredoc body / 2>&1 redirect / trailing positional) and now correctly
extract the PR number.

The remaining limitation:

  Branch-name suffix `7352-tests`: Python `\\b` IS a word boundary at
  digit-to-hyphen (word char `2` to non-word char `-`). The original
  spec claimed `\\b` would reject this; that claim was wrong about
  Python regex semantics. Fixing requires `(?![\\w-])` instead. The
  test is marked xfail(strict=True) so a future tightening that fixes
  it will fail the test (signaling the marker should be removed in lockstep).

The previously-xfail "heredoc body containing a fully-formed `gh pr merge
<N>` substring" case is now FIXED: `_GH_PR_NUMBER_RE` uses the tight
`_GH_FLAG_TOKENS` form for BOTH the pre-subcommand AND post-subcommand
flag walks (rather than reusing the broad `_GH_GLOBAL_FLAGS` for the
pre-subcommand walk). This eliminates the re-anchor-at-second-occurrence
authorization-bypass class. The `test_heredoc_body_with_embedded_gh_pr_merge`
test below is now a regular passing test pinning the fix; the new
`test_authorization_mismatch_attack` test pins the end-to-end attack
shape that the fix prevents.
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
# TRUE GAINS — cases broken on main, fixed by #665
# =============================================================================
# Each test documents the OLD-vs-NEW delta. On main, the regex used the broad
# `_GH_GLOBAL_FLAGS` for the post-subcommand walk, which greedily consumed
# tokens past the PR positional and captured the LAST digit reachable.

class TestGH_PR_NumberRE_TrueGains:
    """The behavioral delta that resolves #665."""

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

    def test_body_file_with_versioned_path(self):
        # Regression-protection only: empirical probe shows OLD ALSO captured
        # "663" here (the path's slashes/hyphens are non-word and `\S+\s+`
        # requires whitespace separation, so the broad walk consumes the path
        # and backtracks to the positional). Pinned as a no-op canary so a
        # future regex change cannot regress this case to capturing "7352".
        cmd = "gh pr merge 663 --body-file /tmp/release-notes-v4.1.7-7352.md --squash"
        assert _capture(cmd) == "663"

    def test_simple_squash_unaffected(self):
        # No regression: simple case still works.
        cmd = "gh pr merge 663 --squash"
        assert _capture(cmd) == "663"

    def test_pre_subcommand_flag_unaffected(self):
        # No regression: --admin between merge subcommand and PR number.
        cmd = "gh pr merge --admin 663 --squash"
        assert _capture(cmd) == "663"

    def test_global_flag_before_subcommand_unaffected(self):
        # No regression: --repo owner/repo as a gh global flag.
        cmd = "gh --repo owner/repo pr merge 663 --squash"
        assert _capture(cmd) == "663"

    def test_close_with_delete_branch_captures_pr(self):
        # No regression: gh pr close form with --delete-branch.
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
# KNOWN LIMITATIONS — pinned with strict xfail
# =============================================================================
# These tests document cases the #665 fix does NOT solve. They are marked
# strict=True so a future tightening that resolves either one will FAIL the
# test (signaling the xfail marker should be removed in the follow-up PR).
#
# Without strict=True, an accidental fix would silently flip xfail→xpass and
# the limitation would be re-rediscovered later. With strict=True, the test
# acts as a tripwire: the limitation is honestly documented as a current
# behavior, and any change to that behavior is forced through review.

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


class TestGH_PR_NumberRE_KnownLimitations:
    """Documented limitations — see module docstring + follow-up issue."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Python `\\b` IS a word boundary at digit-to-hyphen "
            "(word char `2` to non-word char `-`). The earlier spec assumed \\b "
            "would reject `7352-tests` as a suffix match; that assumption was "
            "wrong about Python regex semantics. Fix requires `(?![\\w-])` "
            "instead of `\\b`. Tracked for follow-up."
        ),
    )
    def test_branch_name_with_digit_prefix_suffix_match(self):
        # If a user passes a branch name (instead of PR number) like `7352-tests`,
        # the regex captures the leading digit run. Whether this is dangerous
        # depends on the AskUserQuestion authorization flow, which is itself
        # PR-number-oriented (so a branch-name merge is already an unusual path).
        cmd = "gh pr merge 7352-tests --squash"
        assert _capture(cmd) is None  # Fix would return None (no clean digit token)


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
