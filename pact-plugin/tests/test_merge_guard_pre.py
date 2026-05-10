"""
Tests for merge_guard_pre._GH_PR_NUMBER_RE — PR-number extraction (#665).

Pins the regex behavior introduced by the #665 fix: the new `_GH_FLAG_TOKENS`
constant restricts the post-subcommand token walk to flag-shaped tokens only
(`-x`, `--long`, optionally `--flag value`). The `\\b` boundary on the (\\d+)
capture rejects suffix matches inside longer alphanumeric tokens.

Each TRUE-GAIN test below cites the exact OLD-vs-NEW behavioral delta — these
are the cases that were broken on main (regex captured a non-PR digit token
from heredoc body / 2>&1 redirect / trailing positional) and now correctly
extract the PR number.

The KNOWN LIMITATIONS section pins two cases the #665 fix does NOT solve.
They are marked xfail(strict=True) so a future tightening that fixes either
one will fail the test (signaling the marker should be removed in lockstep).
Without the strict-xfail marker, fixing the limitation would silently pass
the test and the limitation would be re-rediscovered later.

The two limitations:

  1. Heredoc body containing a fully-formed `gh pr merge <N>` substring:
     `_GH_PREFIX` (line 79 of merge_guard_pre.py) still uses the broad
     `_GH_GLOBAL_FLAGS` because DANGEROUS_PATTERNS shares it. The PR-number
     regex re-anchors at the SECOND `gh pr merge` inside the heredoc body
     and captures the embedded digit. Fixing requires duplicating
     `_GH_PREFIX` into a tighter `_GH_PR_NUMBER_PREFIX` (does not affect
     DANGEROUS_PATTERNS).

  2. Branch-name suffix `7352-tests`: Python `\\b` IS a word boundary at
     digit-to-hyphen (word char `2` to non-word char `-`). The original
     architect spec claimed `\\b` would reject this; that claim was wrong
     about Python regex semantics. Fixing requires `(?![\\w-])` instead.

Both limitations are tracked for follow-up post-merge.
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
        # OLD captured "7352" from path filename.
        # NEW correctly captures "663".
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

class TestGH_PR_NumberRE_KnownLimitations:
    """Documented limitations — see module docstring + follow-up issue."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Limitation 1: heredoc body containing a fully-formed `gh pr merge <N>` "
            "substring re-anchors the regex at the second occurrence and captures "
            "the embedded digit. Root cause: _GH_PREFIX uses broad _GH_GLOBAL_FLAGS "
            "(shared with DANGEROUS_PATTERNS). Fix requires duplicating _GH_PREFIX "
            "into a tighter _GH_PR_NUMBER_PREFIX. Tracked for follow-up."
        ),
    )
    def test_heredoc_body_with_embedded_gh_pr_merge(self):
        cmd = 'gh pr merge 663 --body "see also gh pr merge 999 example"'
        # Expected behavior under fix: capture 663 (the real positional).
        # Current behavior: captures 999 (the embedded substring).
        assert _capture(cmd) == "663"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Limitation 2: Python `\\b` IS a word boundary at digit-to-hyphen "
            "(word char `2` to non-word char `-`). Architect spec assumed \\b "
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
