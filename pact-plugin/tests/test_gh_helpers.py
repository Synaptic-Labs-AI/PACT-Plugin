"""
Tests for shared/gh_helpers.py -- gh CLI wrappers, fail-open by construction.

Coverage:

check_pr_state():
 1. Returns "OPEN" / "MERGED" / "CLOSED" on successful gh pr view
 2. Uppercases a lowercase state string
 3. Strips trailing newline from gh stdout
 4. Returns "" when gh is not installed (FileNotFoundError)
 5. Returns "" when gh times out (subprocess.TimeoutExpired)
 6. Returns "" on OSError (e.g. permission denied, unexpected OS error)
 7. Returns "" when gh exits non-zero (e.g. auth expired, not a repo, PR not found)
 8. Accepts int pr_number (coerced to str for subprocess)
 9. Accepts str pr_number (passed through)
10. subprocess.run is invoked with the expected argv (gh pr view N --json state --jq .state)
11. subprocess.run timeout kwarg is 5 seconds (the documented latency cap)
12. capture_output and text kwargs both set (the documented subprocess contract)

Public API / module shape:
13. check_pr_state is importable from shared.gh_helpers (public API pin)
14. No leading-underscore private prefix (distinct from session_resume._check_pr_state)

Cross-module backcompat (#453 DC3 alias path):
15. session_resume._check_pr_state delegates to gh_helpers.check_pr_state
16. The delegating wrapper preserves return-value semantics end-to-end
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# check_pr_state() -- happy path
# ---------------------------------------------------------------------------


class TestCheckPrStateGhHelpersHappyPath:
    """Direct tests for shared.gh_helpers.check_pr_state happy-path semantics."""

    def test_returns_open_for_open_pr(self):
        """Returns 'OPEN' when gh pr view reports OPEN with rc=0."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            assert check_pr_state(42) == "OPEN"

    def test_returns_merged_for_merged_pr(self):
        """Returns 'MERGED' when gh pr view reports MERGED with rc=0."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="MERGED\n")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            assert check_pr_state(77) == "MERGED"

    def test_returns_closed_for_closed_pr(self):
        """Returns 'CLOSED' when gh pr view reports CLOSED with rc=0."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="CLOSED\n")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            assert check_pr_state(99) == "CLOSED"

    def test_uppercases_lowercase_state(self):
        """Lowercase state from gh is normalized to uppercase.

        Pins the `.upper()` in the return path. Old gh releases and some
        JSON clients can return canonical states in lowercase; the
        detector's string comparison at session_end.py:157 is
        uppercase-sensitive, so the wrapper MUST normalize.
        """
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="open\n")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            assert check_pr_state(42) == "OPEN"

    def test_strips_trailing_newline(self):
        """Trailing newline from gh stdout is stripped before uppercase.

        Without .strip() a match on "MERGED" would miss "MERGED\\n" and
        the detector would fall through to the conservative warn branch.
        """
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="MERGED\n")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            result = check_pr_state(42)

        assert result == "MERGED"
        assert "\n" not in result


# ---------------------------------------------------------------------------
# check_pr_state() -- fail-open (SACROSANCT invariant)
# ---------------------------------------------------------------------------


class TestCheckPrStateGhHelpersFailOpen:
    """Fail-open contract: every raisable error path returns ""."""

    def test_returns_empty_on_file_not_found(self):
        """T19 at source: FileNotFoundError (gh not installed) → ""."""
        from shared.gh_helpers import check_pr_state

        with patch(
            "shared.gh_helpers.subprocess.run",
            side_effect=FileNotFoundError("gh not found"),
        ):
            assert check_pr_state(42) == ""

    def test_returns_empty_on_timeout(self):
        """T20 at source: subprocess.TimeoutExpired (slow network) → ""."""
        from shared.gh_helpers import check_pr_state

        with patch(
            "shared.gh_helpers.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
        ):
            assert check_pr_state(42) == ""

    def test_returns_empty_on_oserror(self):
        """T21 at source: OSError (permission denied, ENOMEM, etc.) → ""."""
        from shared.gh_helpers import check_pr_state

        with patch(
            "shared.gh_helpers.subprocess.run",
            side_effect=OSError("permission denied"),
        ):
            assert check_pr_state(42) == ""

    def test_returns_empty_on_nonzero_exit(self):
        """Non-zero gh exit (auth expired, not a repo, PR not found) → ""."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=1, stdout="")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            assert check_pr_state(42) == ""

    def test_returns_empty_on_nonzero_exit_with_stdout(self):
        """Non-zero exit is a fail path even if stdout has a plausible state.

        gh sometimes writes partial output to stdout alongside an error
        on stderr; the wrapper MUST gate on returncode==0 and not fall
        through on stdout content alone.
        """
        from shared.gh_helpers import check_pr_state

        # Adversarial: rc != 0 but stdout looks like a state.
        mock_result = MagicMock(returncode=2, stdout="OPEN\n")
        with patch("shared.gh_helpers.subprocess.run", return_value=mock_result):
            assert check_pr_state(42) == ""


# ---------------------------------------------------------------------------
# check_pr_state() -- input coercion + subprocess contract
# ---------------------------------------------------------------------------


class TestCheckPrStateGhHelpersSubprocessContract:
    """Pin the subprocess argv and kwargs the wrapper passes through."""

    def test_accepts_int_pr_number(self):
        """int pr_number is coerced to str in the gh argv."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch(
            "shared.gh_helpers.subprocess.run", return_value=mock_result
        ) as mock_sub:
            check_pr_state(42)

        call_argv = mock_sub.call_args[0][0]
        # argv layout: gh, pr, view, <pr_number>, --json, state, --jq, .state
        assert call_argv[3] == "42"

    def test_accepts_string_pr_number(self):
        """str pr_number is passed through unchanged."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch(
            "shared.gh_helpers.subprocess.run", return_value=mock_result
        ) as mock_sub:
            check_pr_state("42")

        call_argv = mock_sub.call_args[0][0]
        assert call_argv[3] == "42"

    def test_subprocess_argv_matches_expected_form(self):
        """Pins the exact argv: `gh pr view {n} --json state --jq .state`.

        An accidental argv change (e.g. dropping --jq) would still parse
        valid JSON but return the full object instead of the bare state
        string; the detector comparison against "MERGED"/"CLOSED" would
        then silently miss the short-circuit. Lock the argv shape.
        """
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch(
            "shared.gh_helpers.subprocess.run", return_value=mock_result
        ) as mock_sub:
            check_pr_state(42)

        argv = mock_sub.call_args[0][0]
        assert argv == [
            "gh",
            "pr",
            "view",
            "42",
            "--json",
            "state",
            "--jq",
            ".state",
        ]

    def test_subprocess_timeout_kwarg_is_five_seconds(self):
        """Pin the 5-second timeout cap (documented latency ceiling)."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch(
            "shared.gh_helpers.subprocess.run", return_value=mock_result
        ) as mock_sub:
            check_pr_state(42)

        kwargs = mock_sub.call_args[1]
        assert kwargs.get("timeout") == 5

    def test_subprocess_capture_output_and_text_set(self):
        """Pin capture_output=True and text=True to match the stdout string contract."""
        from shared.gh_helpers import check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch(
            "shared.gh_helpers.subprocess.run", return_value=mock_result
        ) as mock_sub:
            check_pr_state(42)

        kwargs = mock_sub.call_args[1]
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True


# ---------------------------------------------------------------------------
# Public API pin
# ---------------------------------------------------------------------------


class TestGhHelpersPublicAPI:
    """Pin the public API shape of shared.gh_helpers."""

    def test_check_pr_state_importable_from_shared(self):
        """check_pr_state MUST be importable from shared.gh_helpers.

        #453 T25: a regression that renamed or privatized the function
        would break session_end.py's `from shared.gh_helpers import
        check_pr_state` import at module load time, which would cascade
        to an ImportError the SessionEnd hook cannot recover from.
        """
        from shared.gh_helpers import check_pr_state

        assert callable(check_pr_state)

    def test_check_pr_state_has_no_leading_underscore(self):
        """Public-API name pin: no leading underscore.

        The session_resume predecessor was `_check_pr_state` (module-
        private). Elevation to shared/ carries a public-name rename.
        A regression that re-privatized the name would silently break
        callers that imported the public symbol.
        """
        import shared.gh_helpers as gh_helpers

        assert hasattr(gh_helpers, "check_pr_state")
        assert not hasattr(gh_helpers, "_check_pr_state")


# ---------------------------------------------------------------------------
# session_resume backcompat alias (#453 DC3)
# ---------------------------------------------------------------------------


class TestSessionResumeAliasBackcompat:
    """Pin the DC3 alias path: session_resume._check_pr_state → gh_helpers.check_pr_state.

    The architect's DC3 left alias-vs-rename to CODE; coder chose alias
    (lower-risk, preserves existing test patches). These tests pin the
    alias plumbing so a future refactor that drops the wrapper without
    rename-sweeping callers is caught.
    """

    def test_session_resume_check_pr_state_delegates_to_gh_helpers(self):
        """When session_resume._check_pr_state is called, the shared re-export runs.

        Patching shared.check_pr_state (the re-exported public API symbol)
        to a sentinel value and invoking session_resume._check_pr_state
        MUST return the sentinel. Confirms the wrapper is a true delegation
        through the shared package surface, not a duplicate implementation.

        Post-cycle-2 M3: session_resume._check_pr_state imports from
        `shared` (the re-export), not `shared.gh_helpers` directly, so
        the correct patch target is the shared-package binding. Patching
        at shared.gh_helpers would miss because the re-export copies the
        reference at __init__ import time.
        """
        from shared import session_resume

        with patch(
            "shared.check_pr_state", return_value="SENTINEL"
        ) as mock_gh:
            result = session_resume._check_pr_state(42)

        assert result == "SENTINEL"
        mock_gh.assert_called_once_with(42)

    def test_session_resume_wrapper_preserves_return_values_end_to_end(self):
        """End-to-end: session_resume._check_pr_state returns whatever gh would.

        Patches the underlying subprocess (not the helper) so the real
        wrapper + real gh_helpers code runs. Pins that the delegation
        chain preserves OPEN/MERGED/CLOSED/"" semantics unchanged.
        """
        from shared import session_resume

        for state_in, expected in [
            ("OPEN\n", "OPEN"),
            ("MERGED\n", "MERGED"),
            ("CLOSED\n", "CLOSED"),
        ]:
            mock_result = MagicMock(returncode=0, stdout=state_in)
            with patch(
                "shared.gh_helpers.subprocess.run", return_value=mock_result
            ):
                assert session_resume._check_pr_state(42) == expected

    def test_session_resume_wrapper_preserves_fail_open_sentinel(self):
        """End-to-end fail-open: subprocess errors still surface "" through the wrapper."""
        from shared import session_resume

        with patch(
            "shared.gh_helpers.subprocess.run",
            side_effect=FileNotFoundError("gh not found"),
        ):
            assert session_resume._check_pr_state(42) == ""
