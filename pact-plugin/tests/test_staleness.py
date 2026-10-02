"""
Tests for check_pinned_staleness() in session_init.py.

Tests cover:
1. No pinned context section -- no-op
2. Pinned context with recent PR -- not flagged
3. Pinned context with old PR -- flagged stale
4. Pinned context without PR dates -- skipped
5. Over budget -- warning comment added, and refreshed to match later edits
6. Under budget -- no warning, and an earlier warning removed
7. Already-marked stale entries -- not double-marked
8. Multiple entries with mixed staleness
9. _estimate_tokens twin copy equivalence (staleness.py vs working_memory.py)
"""

import ast
import inspect
import os
import re
import tempfile
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# THE ONE SPELLING OF THE WARNING MARKER, imported rather than repeated. Every
# assertion and fixture below is built from this, so a rename in the source
# cannot leave this file describing text the module no longer writes.
#
# THE FAILURE IT PREVENTS IS ASYMMETRIC, and that asymmetry is the whole reason
# to import rather than copy. A POSITIVE assertion against a stale literal fails
# loudly and tells you what happened. A NEGATIVE one passes VACUOUSLY: the old
# text is absent because nothing writes it any more, which is exactly what the
# assertion was written to celebrate. A suite that goes green on a renamed
# constant is worse than one that goes red.
#
# THE SHAPE COMES ACROSS UNCOMPILED, AND THAT IS DELIBERATE. It is the source
# string the two anchored predicates are built from. A test that needs to
# recognise a line this module writes composes its own predicate from the
# SHAPE, so that a change to either compiled anchor cannot move the test and
# the subject together. See `_WARNING_LINE_RE` for the arm that depends on it.
from staleness import _BUDGET_WARNING_PREFIX, _BUDGET_WARNING_SHAPE

# Pulls the token figure out of a warning comment, so a test can compare the
# number in the file against the number in the returned status string.
_WARNING_NUMBER_RE = re.compile(re.escape(_BUDGET_WARNING_PREFIX) + r" ~(\d+) tokens")


# Words added above the budget so the fixture clears it comfortably rather than
# at the boundary. The limit in the docstring below is stated in terms of it.
_OVER_BUDGET_MARGIN_WORDS = 200


def over_budget_body():
    """Build a pin body whose estimated tokens exceed the pinned-context budget.

    THE MAGNITUDE IS DERIVED FROM THE CONSTANT, NEVER WRITTEN AS A LITERAL.
    `estimate_tokens` is `int(words * 1.3)`, so a word count above the budget
    always estimates above the budget, whatever value the budget takes next.

    THE DERIVATION IS WHAT TRACKS A RAISED BUDGET. A hard-coded fixture went
    silently under a raised threshold once already -- it still passed, while no
    longer testing the thing its comment claimed -- and a body built from the
    live constant is what refuses to repeat that.

    WHAT THE ASSERT BELOW GUARDS, WHICH IS NOT THE BUDGET. It fires when
    `int((B + m) * c) <= B`, where B is the budget, m is
    `_OVER_BUDGET_MARGIN_WORDS` and c is the estimator coefficient. At a fixed
    margin that makes it a tripwire on c, not on B. Because `int` truncates,
    that condition is the same as `(B + m) * c < B + 1`, so the closed-form
    boundary is `c < (B + 1) / (B + m)` -- exactly 3201/3400 at the shipped
    values, or approximately 0.9415. Derive that from the fire condition rather
    than trusting the fraction; note in particular that the boundary is NOT
    `B / (B + m)`, and that a decimal is never what the predicate compares
    against. It cannot fire for ANY budget value at the shipped coefficient of
    1.3. It is live and not dead -- drop the coefficient below the boundary and
    this goes red, which a companion test in this file measures rather than
    asserts. Keep it for that reason. Do not read it as a check that the fixture
    still exceeds a raised budget: the derivation above supplies that, and the
    assert adds nothing to it.
    """
    from staleness import PINNED_CONTEXT_TOKEN_BUDGET, estimate_tokens

    # THE EXACT BYTES OF THIS BODY ARE LOAD-BEARING FOR TWO OTHER ARMS, so read
    # this before you change the shape. `test_warning_removed_when_back_under_budget`
    # and `test_warning_removed_when_last_pin_deleted` each call
    # `.replace(over_budget_body(), ...)` in their SETUP. The value below is
    # "word " repeated, so it ENDS WITH A SPACE, and a change that drops that
    # space turns each `.replace` into a no-op. Those arms then fail on their
    # own property assertion, for a cause that has nothing to do with their
    # subject. MEASURED: a whitespace tidy inside the module reddens the two of
    # them by exactly that route, and the red reads as coverage that is absent.
    body = "word " * (PINNED_CONTEXT_TOKEN_BUDGET + _OVER_BUDGET_MARGIN_WORDS)
    assert estimate_tokens(body) > PINNED_CONTEXT_TOKEN_BUDGET, (
        "fixture no longer exceeds the budget it is derived from"
    )
    return body


def warning_number(text):
    """Return the token figure carried by the warning comment, or None."""
    match = _WARNING_NUMBER_RE.search(text)
    return int(match.group(1)) if match else None


class TestOverBudgetBodyAssertIsLive:
    """Non-vacuity for the assert inside `over_budget_body`.

    THE ASSERT CANNOT FIRE AT SHIPPED VALUES, so nothing else in this suite
    shows that it still works. A check that cannot fail on any input the system
    produces is not a check, and this is what makes it a guard again. The same
    discipline is applied to the caps band elsewhere in this file, so this is a
    local convention rather than a new one.

    ONE COUPLING MAKES THE PATCH REACH THE FIXTURE. `over_budget_body` imports
    `estimate_tokens` INSIDE its own body, so the name is looked up on the
    `staleness` module at CALL time and the patch below reaches it. Hoisting
    that import to module scope, or inlining the arithmetic, stops the patch
    reaching it. Both of those were measured, and both make this test go RED
    rather than quiet, because an arm that expects a raise fails when the raise
    stops happening. The coupling cannot be broken silently.

    THE RESIDUAL RISK RUNS THE OTHER WAY: an arm that expects a raise can pass
    on the WRONG raise. The negative arm was checked to fire at the fixture's
    own assert and to carry its message, not some unrelated failure.
    """

    def test_the_assert_fires_when_the_coefficient_drops_below_the_boundary(self):
        """Below `(B + 1) / (B + m)` the fixture must refuse to build."""
        from staleness import PINNED_CONTEXT_TOKEN_BUDGET

        boundary = (PINNED_CONTEXT_TOKEN_BUDGET + 1) / (
            PINNED_CONTEXT_TOKEN_BUDGET + _OVER_BUDGET_MARGIN_WORDS
        )
        below = boundary - 0.01
        with patch(
            "staleness.estimate_tokens",
            lambda text: int(len(text.split()) * below),
        ):
            with pytest.raises(AssertionError):
                over_budget_body()

    def test_the_assert_stays_silent_just_above_the_boundary(self):
        """The positive arm. Without it, a fixture broken for any OTHER reason
        would satisfy the negative arm above and read as a working guard."""
        from staleness import PINNED_CONTEXT_TOKEN_BUDGET

        boundary = (PINNED_CONTEXT_TOKEN_BUDGET + 1) / (
            PINNED_CONTEXT_TOKEN_BUDGET + _OVER_BUDGET_MARGIN_WORDS
        )
        above = boundary + 0.01
        with patch(
            "staleness.estimate_tokens",
            lambda text: int(len(text.split()) * above),
        ):
            assert over_budget_body()

    def test_the_shipped_coefficient_sits_above_the_boundary(self):
        """Pins the docstring's claim that the assert cannot fire as shipped."""
        from staleness import PINNED_CONTEXT_TOKEN_BUDGET, estimate_tokens

        words = PINNED_CONTEXT_TOKEN_BUDGET + _OVER_BUDGET_MARGIN_WORDS
        shipped_coefficient = estimate_tokens("word " * words) / words
        boundary = (PINNED_CONTEXT_TOKEN_BUDGET + 1) / words
        assert shipped_coefficient > boundary, (
            "the estimator coefficient has dropped to the boundary; the "
            "fixture assert is now one edit from firing on shipped values"
        )


class TestCheckPinnedStaleness:
    """Tests for check_pinned_staleness() -- stale pin detection."""

    def _create_project_claude_md(self, tmp_path, content):
        """Helper to create a project CLAUDE.md and patch path resolution."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_no_pinned_section_returns_none(self, tmp_path):
        """Should return None when no Pinned Context section exists."""
        from session_init import check_pinned_staleness

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Working Memory\n"
            "Some working memory content\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is None

    def test_empty_pinned_section_returns_none(self, tmp_path):
        """Should return None when Pinned Context section is empty."""
        from session_init import check_pinned_staleness

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            "## Working Memory\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is None

    def test_no_claude_md_returns_none(self):
        """Should return None when CLAUDE.md does not exist."""
        from session_init import check_pinned_staleness

        with patch("session_init._get_project_claude_md_path", return_value=None), \
             patch("staleness._get_project_claude_md_path", return_value=None), \
             patch("staleness._resolve_project_claude_md_with_base",
                   return_value=(None, None)):
            result = check_pinned_staleness()

        assert result is None

    def test_recent_pr_not_flagged(self, tmp_path):
        """Entries with PR merged within threshold should not be flagged."""
        from session_init import check_pinned_staleness

        # Use a date that is clearly recent (5 days ago)
        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Recent Feature (PR #100, merged {recent_date})\n"
            "- Some details about the feature\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is None
        # File should not be modified
        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- STALE:" not in content

    def test_old_pr_flagged_stale(self, tmp_path):
        """Entries with PR merged beyond threshold should be flagged stale."""
        from session_init import check_pinned_staleness, PINNED_STALENESS_DAYS

        # Use a date well beyond the staleness threshold
        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Details about the old feature\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is not None
        assert "stale" in result.lower()

        # File should have stale marker
        content = claude_md.read_text(encoding="utf-8")
        assert f"<!-- STALE: Last relevant {old_date} -->" in content

    def test_entry_without_pr_date_skipped(self, tmp_path):
        """Entries without PR merge dates should be skipped (not flagged)."""
        from session_init import check_pinned_staleness

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            "### Plugin Architecture\n"
            "- Source repo details\n"
            "- No PR date mentioned here\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is None
        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- STALE:" not in content

    def test_already_stale_entry_not_double_marked(self, tmp_path):
        """Entry already marked stale should not get a second marker (idempotent)."""
        from session_init import check_pinned_staleness, PINNED_STALENESS_DAYS

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        # Marker is placed after the heading (inside entry text), matching
        # the format that check_pinned_staleness() itself produces.
        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            f"<!-- STALE: Last relevant {old_date} -->\n"
            "- Details\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is not None
        assert "stale" in result.lower()

        # Marker count must remain exactly 1 -- no duplicates
        content = claude_md.read_text(encoding="utf-8")
        stale_count = content.count("<!-- STALE:")
        assert stale_count == 1

    def test_over_budget_adds_warning(self, tmp_path):
        """Should add token budget warning when pinned content exceeds budget."""
        from session_init import check_pinned_staleness

        # Pinned content sized from PINNED_CONTEXT_TOKEN_BUDGET, so it stays
        # over budget after any future change to that constant.
        big_content = over_budget_body()

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Big Feature\n{big_content}\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        # Should report budget info
        assert result is not None
        assert "budget" in result.lower()

        # File should have budget warning comment
        content = claude_md.read_text(encoding="utf-8")
        assert _BUDGET_WARNING_PREFIX in content

    def test_under_budget_no_warning(self, tmp_path):
        """Should not add warning when pinned content is within budget.

        A PURE NEGATIVE. This arm asserts only that the warning is ABSENT, and
        absence has many causes: a renamed constant, a broken region parse, or
        the hook never running at all. Measured, with the write path neutered so
        that no warning is ever emitted, this test still PASSED. It cannot fail
        when its instrument is dead, so on its own it is not evidence.

        ITS NON-VACUITY IS ESTABLISHED ELSEWHERE, by
        `TestBudgetWarningInteractions.test_under_budget_absence_is_caused_by_the_budget`,
        which drives the same shape OVER the budget in the same test and
        requires the warning to appear. Read the two together: that arm shows
        the instrument fires, and this one pins the under-budget case. Kept as
        a labelled regression pin rather than repaired in place, so that a
        reviewed assertion is not altered mid-remediation.
        """
        from session_init import check_pinned_staleness

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            "### Small Feature\n"
            "- Just a few words here\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        content = claude_md.read_text(encoding="utf-8")
        assert _BUDGET_WARNING_PREFIX not in content

    def test_mixed_entries_only_old_flagged(self, tmp_path):
        """With mixed recent and old entries, only old ones should be flagged."""
        from session_init import check_pinned_staleness, PINNED_STALENESS_DAYS

        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Recent Feature (PR #100, merged {recent_date})\n"
            "- Recent details\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Old details\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is not None
        assert "1 stale" in result

        content = claude_md.read_text(encoding="utf-8")
        # Only the old entry should have a stale marker
        assert content.count("<!-- STALE:") == 1
        assert f"<!-- STALE: Last relevant {old_date} -->" in content

    def test_pinned_context_at_end_of_file(self, tmp_path):
        """Should handle Pinned Context as the last section (no next section)."""
        from session_init import check_pinned_staleness, PINNED_STALENESS_DAYS

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 5)).strftime("%Y-%m-%d")

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Working Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #99, merged {old_date})\n"
            "- Details here\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        assert result is not None
        assert "stale" in result.lower()


class TestGetProjectClaudeMdPath:
    """Tests for _get_project_claude_md_path() helper."""

    @pytest.fixture
    def clean_env_no_claude_project_dir(self):
        """Remove CLAUDE_PROJECT_DIR from the environment."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True):
            yield

    def test_uses_env_var_when_set(self, tmp_path):
        """Should use CLAUDE_PROJECT_DIR env var first."""
        from session_init import _get_project_claude_md_path

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Test", encoding="utf-8")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = _get_project_claude_md_path()

        assert result == claude_md

    def test_falls_back_to_git_root(self, tmp_path, clean_env_no_claude_project_dir):
        """Should use git root when env var not set.

        --git-common-dir returns the .git directory path; the code resolves
        its parent to get the repo root where CLAUDE.md lives.
        """
        from session_init import _get_project_claude_md_path

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Test", encoding="utf-8")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = str(tmp_path / ".git") + "\n"

        with patch("subprocess.run", return_value=mock_result):
            result = _get_project_claude_md_path()

        assert result == claude_md

    def test_returns_none_when_no_claude_md_found(self, tmp_path, clean_env_no_claude_project_dir):
        """Should return None when CLAUDE.md does not exist anywhere."""
        from session_init import _get_project_claude_md_path

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = _get_project_claude_md_path()

        assert result is None

    def test_finds_dot_claude_via_env_var(self, tmp_path):
        """Should find .claude/CLAUDE.md when CLAUDE_PROJECT_DIR is set."""
        from session_init import _get_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# Test", encoding="utf-8")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = _get_project_claude_md_path()

        assert result == dot_claude

    def test_prefers_dot_claude_over_legacy_via_env_var(self, tmp_path):
        """When both files exist under env var path, .claude/CLAUDE.md wins."""
        from session_init import _get_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# preferred", encoding="utf-8")
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy", encoding="utf-8")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = _get_project_claude_md_path()

        assert result == dot_claude
        assert result != legacy

    def test_finds_dot_claude_via_git_root(self, tmp_path, clean_env_no_claude_project_dir):
        """Should find .claude/CLAUDE.md under the git root when env var unset."""
        from session_init import _get_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# Test", encoding="utf-8")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = str(tmp_path / ".git") + "\n"

        with patch("subprocess.run", return_value=mock_result):
            result = _get_project_claude_md_path()

        assert result == dot_claude

    def test_finds_dot_claude_via_cwd(self, tmp_path, clean_env_no_claude_project_dir):
        """Should find .claude/CLAUDE.md under the current working directory as last resort."""
        from session_init import _get_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# Test", encoding="utf-8")

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = _get_project_claude_md_path()

        assert result == dot_claude

    def test_finds_legacy_when_only_legacy_exists_via_env_var(self, tmp_path):
        """Falls back to legacy ./CLAUDE.md when only it exists."""
        from session_init import _get_project_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# Test", encoding="utf-8")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = _get_project_claude_md_path()

        assert result == legacy


class TestSessionInitEstimateTokens:
    """Tests for _estimate_tokens() in session_init.py (separate copy)."""

    def test_empty_returns_zero(self):
        """Empty string should return 0."""
        from session_init import _estimate_tokens
        assert _estimate_tokens("") == 0

    def test_matches_working_memory_implementation(self):
        """Should produce same results as working_memory._estimate_tokens."""
        from session_init import _estimate_tokens as session_est

        text = "one two three four five six seven eight nine ten"
        assert session_est(text) == 13


class TestBudgetWarningIdempotency:
    """Tests that budget warning is not duplicated on repeated runs."""

    def _create_project_claude_md(self, tmp_path, content):
        """Helper to create a project CLAUDE.md and patch path resolution."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_budget_warning_not_duplicated_on_second_run(self, tmp_path):
        """Running check_pinned_staleness twice on over-budget content should
        produce exactly one <!-- WARNING: comment, not two."""
        from session_init import check_pinned_staleness

        # Sized from PINNED_CONTEXT_TOKEN_BUDGET, so a raised budget cannot
        # leave this fixture under the threshold it claims to exceed.
        big_content = over_budget_body()

        claude_md = self._create_project_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Big Feature\n{big_content}\n\n"
        ))

        with patch("session_init._get_project_claude_md_path", return_value=claude_md):
            # First run -- should add the warning
            result1 = check_pinned_staleness()
            assert result1 is not None
            assert "budget" in result1.lower()

            # Second run -- should NOT add a second warning
            result2 = check_pinned_staleness()

        content_after = claude_md.read_text(encoding="utf-8")
        warning_count = content_after.count(_BUDGET_WARNING_PREFIX)
        assert warning_count == 1, (
            f"Expected exactly 1 budget warning comment, found {warning_count}"
        )


class TestBudgetWarningRefresh:
    """The warning must describe the CURRENT pinned content.

    WHICH ARMS FALSIFY THE OLD BEHAVIOUR, measured against the pre-repair code
    rather than predicted: five of these seven FAIL there. Two PASS both before
    and after -- `test_repeated_runs_do_not_compound` and
    `test_entryless_section_never_gains_a_warning` -- because they guard
    properties of the repair itself, not the defect. Each says so in its own
    docstring. An arm that passes either way proves nothing about a fix unless
    it declares that.
    """

    def _create_project_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def _pinned_doc(self, body, warning=None):
        head = "# Project Memory\n\n## Pinned Context\n\n"
        if warning is not None:
            head += warning
        return f"{head}### Big Feature\n{body}\n\n"

    def test_stale_number_is_refreshed(self, tmp_path):
        """A warning carrying an outdated number must be rewritten. FAILS before.

        The pre-repair guard skipped insertion whenever any warning was
        present, so the first number written stayed forever. A real document
        reported roughly a third of its true size this way.
        """
        from staleness import check_pinned_staleness, estimate_tokens

        body = over_budget_body()
        stale_warning = (
            f"{_BUDGET_WARNING_PREFIX} ~7 tokens (budget: 5). "
            "Consider archiving stale pins. -->\n"
        )
        claude_md = self._create_project_claude_md(
            tmp_path, self._pinned_doc(body, warning=stale_warning)
        )
        # Precondition: the wrong number really is in the parsed region.
        assert warning_number(claude_md.read_text(encoding="utf-8")) == 7

        check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        assert content.count(_BUDGET_WARNING_PREFIX) == 1
        assert warning_number(content) == estimate_tokens(f"### Big Feature\n{body}\n\n")

    def test_status_string_agrees_with_file_comment(self, tmp_path):
        """The returned figure and the written figure must match. FAILS before.

        Before the repair the two came from different measurements: the file
        kept its original number while the status string re-measured a body
        that now contained the warning, so the two disagreed from the second
        run onward by the warning's own word count.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path, self._pinned_doc(over_budget_body())
        )

        check_pinned_staleness(claude_md_path=claude_md)
        status = check_pinned_staleness(claude_md_path=claude_md)

        assert status is not None
        in_status = int(re.search(r"~(\d+) tokens", status).group(1))
        in_file = warning_number(claude_md.read_text(encoding="utf-8"))
        assert in_status == in_file, (
            f"status string reports {in_status} while the file says {in_file}"
        )

    def test_warning_removed_when_back_under_budget(self, tmp_path):
        """A breach that has ended must take its warning with it. FAILS before."""
        from staleness import check_pinned_staleness

        body = over_budget_body()
        claude_md = self._create_project_claude_md(
            tmp_path, self._pinned_doc(body)
        )
        check_pinned_staleness(claude_md_path=claude_md)
        assert _BUDGET_WARNING_PREFIX in claude_md.read_text(encoding="utf-8")

        # The user archives the bulk of the pin.
        shrunk = claude_md.read_text(encoding="utf-8").replace(body, "a short pin")
        claude_md.write_text(shrunk, encoding="utf-8")

        result = check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        assert _BUDGET_WARNING_PREFIX not in content
        assert result is None or "budget" not in result.lower()

    def test_warning_removed_when_last_pin_deleted(self, tmp_path):
        """Deleting every pin must not strand the warning. FAILS before.

        The entry scan returned early when no `### ` heading remained, so the
        warning sat in a section that nothing could reach.
        """
        from staleness import check_pinned_staleness

        body = over_budget_body()
        claude_md = self._create_project_claude_md(
            tmp_path, self._pinned_doc(body)
        )
        check_pinned_staleness(claude_md_path=claude_md)
        assert _BUDGET_WARNING_PREFIX in claude_md.read_text(encoding="utf-8")

        emptied = claude_md.read_text(encoding="utf-8").replace(
            f"### Big Feature\n{body}\n", ""
        )
        claude_md.write_text(emptied, encoding="utf-8")
        assert "### " not in claude_md.read_text(encoding="utf-8")

        check_pinned_staleness(claude_md_path=claude_md)

        assert _BUDGET_WARNING_PREFIX not in claude_md.read_text(
            encoding="utf-8"
        )

    def test_repeated_runs_do_not_compound(self, tmp_path):
        """Three runs give one warning and one unchanging number.

        PASSES BEFORE AND AFTER. This arm guards the repair, not the defect:
        rebuilding the warning on every pass could have let each pass measure
        the previous one and creep upward. It cannot, because the measured body
        never holds a warning.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path, self._pinned_doc(over_budget_body())
        )

        check_pinned_staleness(claude_md_path=claude_md)
        after_first = claude_md.read_text(encoding="utf-8")
        check_pinned_staleness(claude_md_path=claude_md)
        check_pinned_staleness(claude_md_path=claude_md)
        after_third = claude_md.read_text(encoding="utf-8")

        assert after_third.count(_BUDGET_WARNING_PREFIX) == 1
        assert after_third == after_first, "a later pass rewrote a settled document"

    def test_entryless_section_never_gains_a_warning(self, tmp_path):
        """Prose with no `### ` entry stays untouched, over budget or not.

        PASSES BEFORE AND AFTER, and that is the point. The forbidden direction
        is a section carrying NO line of the hook's own shape: whatever its
        size, this code must never start a report in a document it has not
        written to before. This body is ordinary prose, so it stays untouched.

        THE DISCRIMINATION IS THE SHAPE, NOT THE POSITION. The probe now reads
        any line start, so a body whose only warning sits below the head DOES
        enter the pass and gains a current warning above it. That is a ruled-on
        cost, not a violation of the law this test pins. What this test asserts
        is the other case, and the one that must never move: no line of the
        `~N tokens (budget: M)` shape anywhere means no pass and no insertion.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path,
            f"# Project Memory\n\n## Pinned Context\n\n{over_budget_body()}\n\n",
        )
        before = claude_md.read_text(encoding="utf-8")

        result = check_pinned_staleness(claude_md_path=claude_md)

        assert result is None
        assert claude_md.read_text(encoding="utf-8") == before

    def test_user_line_quoting_the_warning_is_preserved(self, tmp_path):
        """A pin body that quotes the warning must survive, and must not silence
        the real one. FAILS before, for a reason worth naming.

        Two properties are asserted here, and only the second was expected to
        need a test. The removal is anchored at the head of the pinned body,
        which is the only place the hook writes a warning, so it cannot reach a
        look-alike line inside a pin. CLAUDE.md is frequently gitignored, so an
        over-broad strip would delete user text no commit could restore.

        The pre-repair presence check searched the WHOLE region for the marker
        as a plain substring, so a pin that merely QUOTED the warning text
        satisfied it and the hook never wrote its own warning at all -- the
        advisory failed open for the entire document on the strength of one
        line of user prose. Anchoring the probe closes that as a side effect,
        which is why this arm asserts a count of two rather than one.
        """
        from staleness import check_pinned_staleness

        quoted = (
            f"{_BUDGET_WARNING_PREFIX} ~99 tokens (budget: 1). "
            "Consider archiving stale pins. -->"
        )
        claude_md = self._create_project_claude_md(
            tmp_path,
            "# Project Memory\n\n## Pinned Context\n\n"
            f"### Doc Pin\nThe hook writes this line:\n{quoted}\n"
            f"{over_budget_body()}\n\n",
        )

        check_pinned_staleness(claude_md_path=claude_md)
        check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        assert quoted in content, "the strip deleted a line a user wrote"
        # One line the hook owns, plus the one the user quoted.
        assert content.count(_BUDGET_WARNING_PREFIX) == 2


class TestStalenessErrorPaths:
    """Tests for error handling paths in staleness.py."""

    def _create_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_read_text_ioerror_returns_none(self, tmp_path):
        """IOError on read_text() (line 116) should return None gracefully."""
        from staleness import check_pinned_staleness

        claude_md = self._create_claude_md(tmp_path, "# Project\n")

        # Patch read_text to raise IOError after the path is validated
        with patch.object(type(claude_md), "read_text", side_effect=IOError("disk error")):
            result = check_pinned_staleness(claude_md_path=claude_md)

        assert result is None

    @pytest.mark.parametrize("due", [False, True])
    def test_a_file_that_is_not_utf8_is_skipped_only_when_a_pin_is_due(
        self, tmp_path, due
    ):
        """The pass plans on a replace-decoded copy and decodes strictly only
        before it writes. It reports the skip only when it would have marked a
        pin, and never writes replacement characters."""
        from staleness import (
            _UNDECODABLE_SKIP, check_pinned_staleness, PINNED_STALENESS_DAYS,
        )

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")
        pin = f"### Old Feature (PR #50, merged {old_date})\n" if due else "### Current\n"
        text = f"# Project Memory\n\n## Pinned Context\n\n{pin}- Details\n\n"

        control = self._create_claude_md(tmp_path, text)
        check_pinned_staleness(claude_md_path=control)
        assert (control.read_text(encoding="utf-8") != text) is due, (
            "control: the valid file's rewrite did not match the arm"
        )

        before = text.encode("utf-8") + b"caf\xe9\n"
        control.write_bytes(before)
        result = check_pinned_staleness(claude_md_path=control)

        assert result == (_UNDECODABLE_SKIP if due else None)
        assert control.read_bytes() == before

    def test_write_text_ioerror_returns_error_message(self, tmp_path):
        """IOError on write_text() (line 218) should return an error message string."""
        from staleness import check_pinned_staleness, PINNED_STALENESS_DAYS
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        claude_md = self._create_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Details\n\n"
        ))

        # Let read_text work normally, but make the write fail. staleness.py
        # writes via claude_md_manager._atomic_write_text (temp + rename), not
        # Path.write_text -- patching write_text here would no-op silently.
        import shared.claude_md_manager as cmm
        with patch.object(cmm, "_atomic_write_text", side_effect=IOError("read-only fs")):
            result = check_pinned_staleness(claude_md_path=claude_md)

        # Should return an error message string (not None)
        assert result is not None
        assert "Failed to update pinned staleness" in result
        # THE CAUSE TOKEN COMES FROM A CLOSED VOCABULARY, so the caller's own
        # message ("read-only fs") is NOT echoed. This assertion used to
        # require that echo, which is the behaviour that leaked the absolute
        # CLAUDE.md path: an OSError renders as `[Errno NN] <strerror>: path`
        # and a cut of it keeps the leading characters, path included.
        assert "OSError" in result
        assert "read-only fs" not in result
        assert "/" not in result

    def test_write_failure_names_its_cause_when_the_error_carries_an_errno(
        self, tmp_path
    ):
        """The cause REACHES THE USER, which the arm above cannot show.

        WHY THIS ARM IS SEPARATE. The arm above injects
        `IOError("read-only fs")`, a SYNTHETIC exception with `errno = None`,
        so it renders as a bare `OSError`. THAT IS A PROPERTY OF THE TEST
        INPUT AND NOT OF THE CONVENTION, and reading only that arm makes the
        message look uninformative. A real read-only filesystem raises with
        `errno = EROFS`.

        SO THIS ARM DRIVES THE PRODUCTION SHAPE: the user reads
        `Failed to update pinned staleness: OSError (EROFS)`, which names the
        cause and selects the repair (remount, or move the project), while
        carrying no path.
        """
        import errno as errno_mod
        from datetime import datetime, timedelta

        from staleness import PINNED_STALENESS_DAYS, check_pinned_staleness

        old_date = (
            datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)
        ).strftime("%Y-%m-%d")
        claude_md = self._create_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Details\n\n"
        ))

        import shared.claude_md_manager as cmm
        injected = OSError(
            errno_mod.EROFS,
            "Read-only file system",
            "/Users/probe-user/secret-dir/CLAUDE.md",
        )
        with patch.object(cmm, "_atomic_write_text", side_effect=injected):
            result = check_pinned_staleness(claude_md_path=claude_md)

        assert result is not None
        assert result.startswith("Failed to update pinned staleness:")
        assert "OSError (EROFS)" in result, (
            f"the cause did not reach the user: {result!r}"
        )
        assert "/" not in result
        assert "Read-only file system" not in result

    def test_write_text_os_error_returns_error_message(self, tmp_path):
        """OSError on write_text() should also return an error message string."""
        from staleness import check_pinned_staleness, PINNED_STALENESS_DAYS
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        claude_md = self._create_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Details\n\n"
        ))

        import shared.claude_md_manager as cmm
        with patch.object(cmm, "_atomic_write_text", side_effect=OSError("permission denied")):
            result = check_pinned_staleness(claude_md_path=claude_md)

        assert result is not None
        assert "Failed to update pinned staleness" in result

    def test_successful_update_normalizes_file_mode_to_0o600(self, tmp_path):
        """A successful staleness update clamps the file mode to 0o600.

        staleness.py's write site calls _atomic_write_text, which chmods the temp
        to 0o600 before os.replace, so a pre-existing 0o644 file is clamped to
        0o600 on update. This pins the SITE behaviour (that check_pinned_staleness
        actually invokes the clamp) — distinct from the helper's clamp (covered by
        test_claude_md_manager.py's migration test) and the create path
        (test_created_file_has_secure_permissions). Before this site switched from
        bare write_text to _atomic_write_text it left the existing mode alone, so
        the site normalization was asserted only in a commit message.

        The explicit chmod 0o644 (NOT an ambient-umask default) is load-bearing:
        it proves the update CHANGES 0o644 -> 0o600. A test relying on the umask
        to make the pre-file non-0o600 would pass trivially under umask 077 —
        itself a latent phantom-green.

        Revert-coupled: reverting the write site to bare write_text leaves the
        0o644 mode untouched and turns this test RED.
        """
        import stat
        from staleness import check_pinned_staleness, PINNED_STALENESS_DAYS
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        claude_md = self._create_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Details\n\n"
        ))
        # Explicit non-0o600 starting mode so the post-update assertion proves a
        # CLAMP, not an ambient-umask coincidence.
        claude_md.chmod(0o644)
        assert stat.S_IMODE(claude_md.stat().st_mode) == 0o644, (
            "precondition: the seeded file must start at 0o644"
        )

        result = check_pinned_staleness(claude_md_path=claude_md)

        # The stale pin triggered a real update (the write path ran), not a
        # no-op skip — so the mode assertion below observes a write, not the seed.
        assert result is not None
        # SITE behaviour: the successful update landed the file at 0o600, clamping
        # the 0o644 it started with.
        final_mode = stat.S_IMODE(claude_md.stat().st_mode)
        assert final_mode == 0o600, (
            f"staleness update must clamp the file mode to 0o600, got {oct(final_mode)}"
        )


class TestStalenessModuleDirect:
    """Tests for staleness.py called directly (not via session_init wrapper)."""

    def _create_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_explicit_path_parameter(self, tmp_path):
        """check_pinned_staleness(claude_md_path=...) should use the given path
        without calling _get_project_claude_md_path."""
        from staleness import check_pinned_staleness, PINNED_STALENESS_DAYS

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        claude_md = self._create_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old_date})\n"
            "- Details\n\n"
        ))

        # Call with explicit path -- should NOT need _get_project_claude_md_path
        with patch("staleness._get_project_claude_md_path") as mock_get:
            result = check_pinned_staleness(claude_md_path=claude_md)

        # The path resolver should never have been called
        mock_get.assert_not_called()
        # But the stale entry should still be detected
        assert result is not None
        assert "stale" in result.lower()

        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- STALE:" in content

    def test_entry_with_no_newline_after_heading_skipped(self, tmp_path):
        """An entry whose heading has no trailing newline (single-line entry)
        should be skipped gracefully by the .find('\n') guard."""
        from staleness import check_pinned_staleness, PINNED_STALENESS_DAYS

        old_date = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")

        # Construct pinned content where the LAST entry has no trailing newline.
        # This means entry_text.find("\n") returns -1 and the code should skip it.
        claude_md = self._create_claude_md(tmp_path, (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Heading-only entry (PR #80, merged {old_date})"
        ))

        result = check_pinned_staleness(claude_md_path=claude_md)

        # The entry should be skipped (no stale marker added) because there is
        # no newline after the heading to insert the marker after.
        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- STALE:" not in content

    def test_nonexistent_explicit_path_returns_none(self, tmp_path):
        """Passing a path to a non-existent file should return None gracefully."""
        from staleness import check_pinned_staleness

        missing = tmp_path / "does_not_exist.md"
        result = check_pinned_staleness(claude_md_path=missing)
        assert result is None


class TestDeclaredPinnedEndMarker:
    """The declared END boundary of `## Pinned Context`.

    WHY THIS CLASS EXISTS AT ALL, stated first because it is the trap. The
    declared parse is a NO-OP on every document that carries the shipped
    marker name: `PINNED_END_MARKER` sits in the `PACT_MEMORY_` family, so the
    INFERRED forward scan already stops at its line and the two offsets
    coincide. A suite that only feeds documents to the parser therefore
    certifies the change as a no-op, which it is, and PROVES NOTHING.

    THE NON-VACUITY ARM IS `test_rename_arm_*` BELOW, AND IT MUTATES THE
    CONSTANT, NOT THE DOCUMENT. That is the only configuration in which the
    declared parse and the inferred scan disagree. Any future edit that drops
    that arm leaves this class unable to fail for the right reason.
    """

    def _doc(self, pinned_body, above="", tail="## Working Memory\nnotes\n"):
        """`above` goes between `## Retrieved Context` and the pinned heading.

        THE START MARKER BELONGS THERE, NOT IN `pinned_body`. It carries the
        `PACT_MEMORY_` prefix, so a copy placed as the body's first line
        TERMINATES the scan at once and the section parses as empty (None).
        The writer emits it immediately ABOVE the heading, and a fixture that
        puts it below is testing a document the writer cannot produce.
        """
        from shared.claude_md_manager import (
            MANAGED_START_MARKER, MANAGED_END_MARKER,
        )
        return (
            "# Project\npreamble\n"
            + MANAGED_START_MARKER + "\n"
            "# PACT Framework and Managed Project Memory\n\n"
            "## Retrieved Context\n\n"
            + above
            + "## Pinned Context\n"
            + pinned_body + tail
            + MANAGED_END_MARKER + "\ntail\n"
        )

    PIN_A = "<!-- pinned: 2026-01-01 -->\n### Alpha\nalpha body\n\n"
    PIN_B = "<!-- pinned: 2026-01-02 -->\n### Beta\nbeta body\n\n"

    def test_rename_arm_declared_parse_excludes_what_inferred_scan_charges(
        self, monkeypatch
    ):
        """THE NON-VACUITY ARM. Rename the marker out of the boundary family
        and the two parses diverge: the inferred scan overruns the marker and
        charges its text to the pinned body, while the declared parse still
        ends the region at it.

        Today the marker escapes the body only because its NAME matches a
        generic alternation -- an INCIDENTAL exclusion. This arm is what makes
        the exclusion INTENTIONAL, and it is the sole measured difference
        between the two parses.
        """
        import staleness

        renamed = "<!-- PINNED_REGION_END -->"
        # CONTROL: the rename must actually leave the family, or this arm
        # silently degrades into the no-op case it exists to escape.
        from shared.claude_md_manager import PACT_BOUNDARY_PREFIXES
        assert not any(
            renamed.startswith("<!-- " + p) for p in PACT_BOUNDARY_PREFIXES
        ), "rename arm is vacuous: the substitute marker is still in the family"

        content = self._doc(self.PIN_A + self.PIN_B + renamed + "\n")

        # Arm 1: shipped constant -> the substitute is not the declared end,
        # so the parse falls through to the inferred scan and CHARGES it.
        inferred_body = staleness._parse_pinned_section(content)[2]

        # Arm 2: the constant IS the substitute -> the declared parse excludes.
        monkeypatch.setattr(staleness, "PINNED_END_MARKER", renamed)
        declared_body = staleness._parse_pinned_section(content)[2]

        assert renamed in inferred_body, (
            "inferred scan should have overrun the out-of-family marker"
        )
        assert renamed not in declared_body, (
            "declared parse must exclude the marker it was told to look for"
        )
        assert len(declared_body) < len(inferred_body), (
            f"declared ({len(declared_body)}) must be shorter than inferred "
            f"({len(inferred_body)}); equal lengths mean the arm went vacuous"
        )

    def test_absent_end_marker_uses_the_inferred_scan(self):
        """Fail open. No marker at all is the production shape on day one."""
        import staleness
        with_marker = staleness._parse_pinned_section(
            self._doc(self.PIN_A + self.PIN_B
                      + staleness.PINNED_END_MARKER + "\n")
        )[2]
        without = staleness._parse_pinned_section(
            self._doc(self.PIN_A + self.PIN_B)
        )[2]
        assert with_marker == without

    def test_start_marker_without_end_does_not_raise_and_parses_the_same(self):
        """Half-marked tolerance is REQUIRED, not optional. If the marker
        writer lands before a document gains an END, every file it touches
        carries a START alone. That is correct, not broken."""
        import staleness
        from shared.claude_md_manager import PINNED_START_MARKER

        half = self._doc(self.PIN_A + self.PIN_B,
                         above=PINNED_START_MARKER + "\n")
        plain = self._doc(self.PIN_A + self.PIN_B)

        half_parsed = staleness._parse_pinned_section(half)
        assert half_parsed is not None, (
            "a START-only document must still parse; None means the half-marked "
            "state was treated as absent rather than tolerated"
        )
        assert half_parsed[2] == staleness._parse_pinned_section(plain)[2]

    def test_heading_between_body_and_declared_end_is_not_swallowed(self):
        """A foreign H2 section before the declared end must stay OUT of the
        pinned span.

        THE OUTCOME IS UNCHANGED; THE MECHANISM IS NOT. This case used to be
        caught by a well-formedness gate that probed for headings. The gate is
        gone: the parse now bounds the declared end by the inferred one, so an
        interloping section stops the inferred scan and `min` takes it. The gate
        was an enumeration of bad shapes and it enumerated them too narrowly --
        headings yes, PACT boundary comments no -- which shipped a cardinal
        over-block. This test survives because the PROPERTY survives.

        Do not read a pass here as coverage of the alphabet. See
        `test_span_never_exceeds_inferred_for_every_scan_terminator`, which is
        the arm that ranges over the terminator alternation rather than over the
        one member this test happens to use.
        """
        import staleness

        content = self._doc(
            self.PIN_A + self.PIN_B
            + "## Interloper\nforeign content\n\n"
            + staleness.PINNED_END_MARKER + "\n"
        )
        body = staleness._parse_pinned_section(content)[2]
        assert "foreign content" not in body, (
            "the gate must refuse a declared end that lies beyond a heading"
        )
        assert "## Interloper" not in body

    def test_span_never_exceeds_inferred_for_every_scan_terminator(self):
        """THE CEILING INVARIANT, ranged over the SCAN's alphabet.

        THE INPUT ALPHABET IS DERIVED FROM THE TERMINATOR SCAN, NEVER FROM THE
        PARSE RULE, and that is the whole point of this test rather than a
        stylistic note. The predecessor gate probed `#{1,2}\\s`; the test written
        for it exercised an H2 heading. Its alphabet was read off the
        implementation, so it could not falsify the implementation's CHOICE of
        alphabet -- it exercised exactly the shape the gate already covered, and
        reported green while a boundary comment sailed through. A TEST WHOSE
        INPUT SPACE COMES FROM THE CODE UNDER TEST CANNOT FALSIFY THAT CODE'S
        CHOICE OF INPUT SPACE.

        So the members here are enumerated from `PACT_BOUNDARY_PREFIXES` plus
        the heading form -- the two branches of the scan's own alternation. A
        fourth prefix added to that tuple appears here automatically.

        THE ASSERTION IS THE INVARIANT ITSELF, not a table of expected offsets:
        the span returned for a MARKED document is never larger than the span
        returned for the same document unmarked. Unmarked is the pre-change
        behaviour, so this states that no declared end can widen any region.

        COUNTER-TEST PROTOCOL, TWO MUTANTS WITH DIFFERENT SIGNATURES. Both were
        run; the second is the one that matters:

          MUTANT A -- replace the `min` with a bare unbounded override.
            EVERY member fails, headings included.

          MUTANT B -- restore the shipped defect exactly: a heading-only probe
            `#{1,2}\\s` guarding an unbounded override.
            The HEADING members PASS and all three BOUNDARY members FAIL.

        MUTANT B IS THE PROOF THAT THIS TEST WOULD HAVE CAUGHT THE SHIPPED
        DEFECT, and its asymmetry is the reason the defect shipped: the gate
        covered the heading subset, the test exercised the heading subset, and
        the two agreed with each other while both missed the boundary subset.

        A NOTE ON BUILDING MUTANT B, because getting it wrong reads like a
        result. A first attempt over-escaped the pattern into a literal
        backslash, which matched nothing, silently degrading mutant B into
        mutant A -- every member failed and the asymmetry vanished. The tell was
        that headings failed too. If you rebuild it, first assert the mutant
        pattern MATCHES `## Interloper` and does NOT match a boundary comment;
        otherwise you are testing a bare override and will conclude the wrong
        thing about what this test covers.
        """
        import staleness
        from shared.claude_md_manager import PACT_BOUNDARY_PREFIXES

        members = [
            ("h2 heading", "## Interloper\nforeign\n\n"),
            ("h1 heading", "# Interloper\nforeign\n\n"),
        ] + [
            (f"boundary comment {p}", f"<!-- {p}NOTE -->\nforeign\n\n")
            for p in PACT_BOUNDARY_PREFIXES
        ]
        assert len(members) >= 5, "alphabet collapsed; the sweep is vacuous"

        for label, interloper in members:
            marked = staleness._parse_pinned_section(
                self._doc(self.PIN_A + self.PIN_B + interloper
                          + staleness.PINNED_END_MARKER + "\n")
            )
            inferred = staleness._parse_pinned_section(
                self._doc(self.PIN_A + self.PIN_B + interloper)
            )
            assert marked is not None and inferred is not None, label
            assert len(marked[2]) <= len(inferred[2]), (
                f"{label}: the declared end WIDENED the region "
                f"({len(marked[2])} > {len(inferred[2])}). A declared end must "
                f"only ever bound the inferred one."
            )
            assert "foreign" not in marked[2], (
                f"{label}: foreign section pulled into the pinned span"
            )

    def test_indented_marker_quoted_in_a_pin_body_does_not_truncate(self):
        """THE FAIL-OPEN GUARD, and it is the reason
        `_find_declared_end_offset` tolerates trailing whitespace only.

        `_find_terminator_offset` matches the RAW line, so it does not match an
        indented marker. A `.strip()` compare in the declared locator WOULD,
        the two would disagree about which line ends the region, and the
        declared offset would land INSIDE the pinned body -- dropping a pin out
        of the counted span and failing the cap OPEN.

        Counter-test protocol: change `.rstrip()` to `.strip()` in
        `staleness._find_declared_end_offset` and this test must fail.
        """
        import staleness

        content = self._doc(
            "<!-- pinned: 2026-01-01 -->\n### Alpha\ntext\n"
            "  " + staleness.PINNED_END_MARKER + "\n"
            "more alpha\n\n"
            + self.PIN_B
        )
        body = staleness._parse_pinned_section(content)[2]
        assert body.count("### ") == 2, (
            f"expected both pins to stay in the counted span, got "
            f"{body.count('### ')} -- an indented quote truncated the region"
        )

    def test_trailing_whitespace_on_the_marker_line_is_tolerated(self):
        """The other half of the same asymmetry. A trailing-space marker line
        is still at column 0, so the inferred scan matches it too and the two
        locators stay in agreement. Tolerating it costs nothing."""
        import staleness

        padded = staleness._parse_pinned_section(
            self._doc(self.PIN_A + self.PIN_B
                      + staleness.PINNED_END_MARKER + "   \n")
        )[2]
        clean = staleness._parse_pinned_section(
            self._doc(self.PIN_A + self.PIN_B
                      + staleness.PINNED_END_MARKER + "\n")
        )[2]
        assert padded == clean


class TestParsePinnedSectionMarkerBoundary:
    r"""Direct unit tests for `staleness._parse_pinned_section`'s marker-aware
    section-end detection.

    An INTEGRATION test in `TestCheckPinnedStaleness` passes even if the
    next_section regex is relaxed back to `^#{1,2}\s`, whenever its fixture
    content has no stale entries, because no write-back occurs and the marker
    is never touched. A unit-level test on `_parse_pinned_section`
    directly asserts that the returned `pinned_end` index stops BEFORE the
    marker — this is the differentiated behavior the round-3 fix protects.

    Counter-test protocol: if the `next_section` regex in
    `staleness._parse_pinned_section` is reverted to `^#{1,2}\s`, this test
    must fail because the returned `pinned_end` would overshoot into the
    marker and any downstream content.
    """

    def test_pinned_end_stops_before_pact_memory_end_marker(self):
        """`_parse_pinned_section` must return `pinned_end` at the line that
        begins with `<!-- PACT_MEMORY_END -->`, not past it.

        Without the marker alternative in the regex, the parser would scan
        past the marker looking for the next H1/H2 heading, and `pinned_end`
        would either land on that later heading or on EOF — both causing
        subsequent write-back to eat the boundary marker.
        """
        from staleness import _parse_pinned_section

        content = (
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            "<!-- PACT_MEMORY_START -->\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "### Some pin (PR #100, merged 2026-04-01)\n"
            "Pin body content.\n"
            "<!-- PACT_MEMORY_END -->\n"
            "\n"
            "<!-- PACT_MANAGED_END -->\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        pinned_start, pinned_end, pinned_content = result

        # The returned pinned_content must contain the pin body but must
        # STOP before the marker line. The marker itself must NOT appear
        # inside the extracted pinned_content.
        assert "### Some pin" in pinned_content
        assert "Pin body content." in pinned_content
        assert "<!-- PACT_MEMORY_END -->" not in pinned_content
        assert "<!-- PACT_MANAGED_END -->" not in pinned_content

        # pinned_end must point to the start of the PACT_MEMORY_END marker
        # line, not past it. The character at content[pinned_end] should
        # be the start of the `<!-- PACT_MEMORY_END -->` marker.
        marker_idx = content.index("<!-- PACT_MEMORY_END -->")
        assert pinned_end == marker_idx, (
            f"pinned_end ({pinned_end}) should point to the PACT_MEMORY_END "
            f"marker start ({marker_idx}), not past it"
        )

    def test_pinned_end_stops_before_pact_managed_end_marker(self):
        """Same boundary behavior for PACT_MANAGED_END when the memory
        marker is absent (e.g., malformed file missing PACT_MEMORY_END).
        """
        from staleness import _parse_pinned_section

        content = (
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            "## Pinned Context\n"
            "### Pin one\n"
            "Body one.\n"
            "<!-- PACT_MANAGED_END -->\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _, pinned_end, pinned_content = result

        assert "Pin one" in pinned_content
        assert "<!-- PACT_MANAGED_END -->" not in pinned_content

        marker_idx = content.index("<!-- PACT_MANAGED_END -->")
        assert pinned_end == marker_idx

    def test_pinned_end_stops_before_pact_routing_end_marker(self):
        """Same boundary behavior for PACT_ROUTING_END. This is an edge
        case — normally routing precedes Pinned Context in the file, but
        the regex alternative lists all three prefixes symmetrically.

        Defensive coverage: the regex alternative lists all three boundary
        prefixes (PACT_MEMORY_, PACT_MANAGED_, PACT_ROUTING_) symmetrically,
        and the parser must terminate the pinned section at ANY of them
        even when the canonical file layout would never put PACT_ROUTING_END
        after ## Pinned Context. This test guarantees the invariant holds
        regardless of file layout reshuffling in the future.
        """
        from staleness import _parse_pinned_section

        content = (
            "## Pinned Context\n"
            "### Pin two\n"
            "Body two.\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Some Later Heading\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _, pinned_end, pinned_content = result

        assert "Pin two" in pinned_content
        assert "<!-- PACT_ROUTING_END -->" not in pinned_content

        marker_idx = content.index("<!-- PACT_ROUTING_END -->")
        assert pinned_end == marker_idx

    def test_pinned_end_still_stops_at_heading_when_no_markers(self):
        """Regression: the marker alternative must NOT break the pre-existing
        heading-based boundary when no markers are present. Pre-migration
        files must still parse correctly.
        """
        from staleness import _parse_pinned_section

        content = (
            "## Pinned Context\n"
            "### Pre-migration pin\n"
            "Legacy body.\n"
            "\n"
            "## Working Memory\n"
            "- entry\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _, pinned_end, pinned_content = result

        assert "Pre-migration pin" in pinned_content
        assert "## Working Memory" not in pinned_content

        heading_idx = content.index("## Working Memory")
        assert pinned_end == heading_idx


class TestEstimateTokensEquivalence:
    """Verify _estimate_tokens is identical across its two twin copies.

    Cross-package isolation (hooks/ vs skills/pact-memory/scripts/) prevents
    direct imports between the two packages. The _estimate_tokens function is
    intentionally duplicated as a "twin copy" with cross-reference comments in
    each file. This test ensures the two copies stay in sync by comparing their
    source code via inspect.getsource().

    Twin locations:
    - hooks/staleness.py: estimate_tokens() (public name, aliased as _estimate_tokens)
    - skills/pact-memory/scripts/working_memory.py: _estimate_tokens() (private name)
    """

    def test_function_bodies_are_identical(self):
        """The function body of _estimate_tokens must be identical in both files.

        Uses inspect.getsource() to get the raw source of each function, then
        strips docstrings and normalizes whitespace so that differences in
        function name or docstring wording do not cause false failures. Only
        the executable lines (the actual logic) are compared.
        """
        from staleness import estimate_tokens as staleness_fn
        from scripts.working_memory import _estimate_tokens as working_memory_fn

        staleness_source = inspect.getsource(staleness_fn)
        working_memory_source = inspect.getsource(working_memory_fn)

        staleness_body = self._extract_body(staleness_source)
        working_memory_body = self._extract_body(working_memory_source)

        assert staleness_body == working_memory_body, (
            "Twin copies of _estimate_tokens have diverged.\n"
            f"staleness.py body:\n{staleness_body}\n\n"
            f"working_memory.py body:\n{working_memory_body}"
        )

    def test_both_use_word_count_times_1_3(self):
        """Both copies must use the word_count * 1.3 formula."""
        from staleness import estimate_tokens as staleness_fn
        from scripts.working_memory import _estimate_tokens as working_memory_fn

        staleness_source = inspect.getsource(staleness_fn)
        working_memory_source = inspect.getsource(working_memory_fn)

        for name, source in [("staleness.py", staleness_source),
                             ("working_memory.py", working_memory_source)]:
            assert "text.split()" in source, (
                f"{name}: missing text.split() call"
            )
            assert "* 1.3" in source, (
                f"{name}: missing * 1.3 multiplier"
            )

    def test_both_return_zero_for_empty(self):
        """Both copies must return 0 for empty/falsy input."""
        from staleness import estimate_tokens as staleness_fn
        from scripts.working_memory import _estimate_tokens as working_memory_fn

        assert staleness_fn("") == 0
        assert working_memory_fn("") == 0
        assert staleness_fn("") == working_memory_fn("")

    def test_both_produce_same_result(self):
        """Both copies must produce identical results for the same input."""
        from staleness import estimate_tokens as staleness_fn
        from scripts.working_memory import _estimate_tokens as working_memory_fn

        test_inputs = [
            "",
            "hello",
            "one two three four five six seven eight nine ten",
            "word " * 100,
        ]
        for text in test_inputs:
            assert staleness_fn(text) == working_memory_fn(text), (
                f"Results differ for input: {text[:50]!r}..."
            )

    @staticmethod
    def _extract_body(source: str) -> str:
        """Extract the executable body of a function, stripping docstring and def line.

        Removes the function signature line, any docstring (triple-quoted block),
        and dedents the remaining lines to normalize indentation. This allows
        comparison of the actual logic regardless of function name or doc content.
        """
        lines = source.split("\n")

        # Skip the def line
        body_lines = lines[1:]

        # Join and dedent
        body_text = textwrap.dedent("\n".join(body_lines)).strip()

        # Remove docstring if present (triple double-quotes or triple single-quotes)
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break

        return body_text


class TestCheckPinnedStalenessHardening:
    """SECURITY + CONCURRENCY hardening for check_pinned_staleness (#366 R5 H1).

    Background: staleness.py is the 6th writer to project CLAUDE.md. The
    other 5 writers (claude_md_manager.{remove_stale_kernel_block,
    update_pact_routing, ensure_project_memory_md} and
    session_resume.update_session_info, plus the test fixture writers)
    use the canonical pattern: file_lock around the read-mutate-write
    block, symlink check INSIDE the lock as a TOCTOU defense, opaque
    skip status when the precondition fails. Pre-fix, check_pinned_staleness
    used a bare read_text/write_text pair with no lock and no symlink
    guard — a concurrent update_session_info or update_pact_routing
    could clobber the SESSION_START block, and a planted symlink would
    redirect the write to an attacker-chosen target.

    This class pins the canonical hardening:
    1. Symlink target rejected with opaque skip status
    2. Concurrent content change detected via inside-lock re-read; no write
       occurs and the function returns None (idempotent skip — staleness
       markers are re-detectable next session, so dropping this pass is
       always safe)
    """

    STALE_DATE = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    def _stale_pinned_content(self):
        """Build CLAUDE.md content with one pinned entry stale enough to
        trigger the modified=True branch of check_pinned_staleness."""
        return (
            "# Project Memory\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #100, merged {self.STALE_DATE})\n"
            "- Some details about the feature\n\n"
            "## Working Memory\n"
        )

    def test_leaf_symlink_out_of_project_allowed_victim_untouched(self, tmp_path):
        """A leaf symlink pointing OUT of the project is ALLOWED, and the write
        lands in-project rather than on the out-of-project victim.

        THIS IS THE TRAP-1 COUPLING TRIPWIRE at this site, and it could not
        exist before the corrected predicate, because the earlier one refused
        this topology outright. It replaces a negative that asserted the
        refusal. That refusal was an over-block: containment is decided on the
        parent chain and the leaf is never consulted. The genuine escape shape
        at this site is a symlinked PARENT, which is covered separately.

        DO NOT 'fix' a failure here by making the guard refuse the topology.
        The victim survives because os.replace is renameat(2) -- it binds the
        final component as a directory ENTRY without following it. A failure
        here means the WRITE SHAPE changed, not that the guard weakened.
        """
        from session_init import check_pinned_staleness

        # Project root is proj_dir (the lexical base of proj_dir/CLAUDE.md).
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()

        # Plant a file OUTSIDE the project root, seeded with stale content so
        # the read-through drives modified=True (the gate into the write path).
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_victim = outside_dir / "external_target.md"
        outside_victim.write_text(self._stale_pinned_content(), encoding="utf-8")

        # project CLAUDE.md is a leaf symlink pointing at that outside file.
        managed_path = proj_dir / "CLAUDE.md"
        os.symlink(str(outside_victim), str(managed_path))
        assert managed_path.is_symlink()

        with patch("session_init._get_project_claude_md_path", return_value=managed_path), \
             patch("staleness._get_project_claude_md_path", return_value=managed_path):
            result = check_pinned_staleness()

        assert result is not None
        assert "skipped" not in result.lower()

        # Boundary 1 -- the out-of-project victim is BYTE-identical: no STALE
        # marker, no budget warning, no write-through. Note this would ALSO hold
        # under a refusal, so it cannot by itself show the write was permitted.
        assert outside_victim.read_text(encoding="utf-8") == self._stale_pinned_content()
        # Boundary 2 -- the write landed at the IN-PROJECT entry, which is now a
        # real file. This is the assertion that pins the ALLOW and the one that
        # flips if the write is ever made to follow the leaf.
        assert not managed_path.is_symlink()
        assert "STALE" in managed_path.read_text(encoding="utf-8")

    def test_in_project_symlink_redirect_allowed_containment_supersedes_ban(self, tmp_path):
        """#1247 deliberate behavior change: an IN-PROJECT symlink redirect is
        ALLOWED. Containment refuses only ESCAPES; an in-project target is
        contained. The former blunt leaf-is_symlink guard refused ALL symlinks
        (an over-block on benign in-project symlinks) — containment removes
        that over-block while still closing the F1 escape.

        Security-engineer-signed-off residual: os.replace REPLACES the symlink
        entry, it does NOT write through it, so even a crafted in-project
        redirect cannot overwrite its pointed-to file. This pins that safety:
        the pointed-to in-project file is byte-unchanged and CLAUDE.md becomes
        a regular file carrying the staleness update.
        """
        from session_init import check_pinned_staleness

        # Both the symlink and its target are IN-PROJECT (project root =
        # tmp_path, the lexical base of tmp_path/CLAUDE.md) -> contained.
        sibling = tmp_path / "sib.md"
        sibling.write_text(self._stale_pinned_content(), encoding="utf-8")
        sibling_before = sibling.read_text(encoding="utf-8")

        managed_path = tmp_path / "CLAUDE.md"
        os.symlink(str(sibling), str(managed_path))
        assert managed_path.is_symlink()

        with patch("session_init._get_project_claude_md_path", return_value=managed_path), \
             patch("staleness._get_project_claude_md_path", return_value=managed_path):
            result = check_pinned_staleness()

        # ALLOWED: the write proceeded (a detection message, not the opaque skip).
        assert result is not None
        assert "skipped" not in result.lower()

        # os.replace does NOT write through the leaf symlink: the pointed-to
        # in-project file is byte-unchanged...
        assert sibling.read_text(encoding="utf-8") == sibling_before
        # ...and CLAUDE.md is now a real file carrying the staleness marker.
        assert not managed_path.is_symlink()
        assert "<!-- STALE:" in managed_path.read_text(encoding="utf-8")

    def test_containment_refusal_attributed_not_lock_contention(self, tmp_path):
        """A containment refusal must surface the CONTAINMENT status, never the
        lock-contention status. ContainmentError subclasses OSError, so if the
        `except ContainmentError` arm were ordered after `except OSError` (or
        dropped), an escape would be silently misattributed to a lock failure.
        This pins the arm ordering end-to-end via the real write path.

        THE TOPOLOGY IS LOAD-BEARING, not incidental scenery. The escape must be
        a symlinked PARENT, never a symlinked leaf. Containment is decided on the
        parent chain and the leaf is never consulted, so a leaf pointing outside
        is ALLOWED and raises nothing -- this test would then go green while
        exercising no refusal path at all, and would keep passing with the
        `except` arms in the wrong order. Only a parent-out topology reaches the
        refusal this test exists to attribute.
        """
        from session_init import check_pinned_staleness

        project = tmp_path / "proj"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
        managed_path = project / ".claude" / "CLAUDE.md"  # resolves into `outside`
        managed_path.write_text(self._stale_pinned_content(), encoding="utf-8")
        before = (outside / "CLAUDE.md").read_text(encoding="utf-8")

        with patch("session_init._get_project_claude_md_path", return_value=managed_path), \
             patch("staleness._get_project_claude_md_path", return_value=managed_path):
            result = check_pinned_staleness()

        assert result == "Pinned staleness skipped: path precondition not met."
        assert "lock contention" not in result.lower()
        # The out-of-project victim is untouched by the refusal.
        assert (outside / "CLAUDE.md").read_text(encoding="utf-8") == before

    def test_concurrent_content_change_skips_write(self, tmp_path, monkeypatch):
        """If content changes between the outer read at L348 and the
        inner re-read inside the lock, the function returns None and does
        NOT write.

        Justification: staleness markers are idempotent — the next session
        will re-detect any stale entries. Better to sacrifice this pass
        than to clobber a concurrent update_pact_routing or
        update_session_info that landed between our outer read and lock
        acquisition.

        We simulate the concurrent change by monkeypatching Path.read_text
        so the SECOND call (inside the lock) returns DIFFERENT content
        than the first.
        """
        from session_init import check_pinned_staleness

        # Plant the project CLAUDE.md with stale content (triggers modified=True)
        claude_md = tmp_path / "CLAUDE.md"
        original_content = self._stale_pinned_content()
        claude_md.write_text(original_content, encoding="utf-8")

        # The "concurrent writer" simulated content — different bytes,
        # represents a SESSION_START block landing between our outer read
        # and our lock acquisition.
        concurrent_content = (
            "# Project Memory\n\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "- Resume: claude --resume abc123\n"
            "<!-- SESSION_END -->\n\n"
            "## Pinned Context\n\n"
            f"### Old Feature (PR #100, merged {self.STALE_DATE})\n"
            "- Some details about the feature\n\n"
        )

        original_read_text = Path.read_text
        call_state = {"count": 0}

        def fake_read_text(self, *args, **kwargs):
            # Only intercept reads of the managed path; let other reads
            # (e.g., site-packages, pyc inspection) pass through.
            if str(self) == str(claude_md):
                call_state["count"] += 1
                if call_state["count"] == 1:
                    return original_content
                # All subsequent reads (the inside-lock re-read) return the
                # "concurrent change" content.
                return concurrent_content
            return original_read_text(self, *args, **kwargs)

        # Track writes so we can assert no write occurred. The managed-path write
        # goes through _atomic_write_text (temp + rename), NOT Path.write_text, so
        # tracking Path.write_text would never fire and the "no write" assertion
        # would pass vacuously whether or not the write was skipped. Track the REAL
        # write seam. staleness.check_pinned_staleness imports _atomic_write_text
        # from shared.claude_md_manager at call time, so patching the module
        # attribute is what its local `from ... import` binds to.
        import shared.claude_md_manager as cmm
        write_calls = []
        real_atomic = cmm._atomic_write_text

        def tracking_atomic(target, content, *args, **kwargs):
            if str(target) == str(claude_md):
                write_calls.append(content)
            return real_atomic(target, content, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        monkeypatch.setattr(cmm, "_atomic_write_text", tracking_atomic)

        with patch("session_init._get_project_claude_md_path", return_value=claude_md), \
             patch("staleness._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        # The inside-lock re-read detected the content change → skip write
        # → return None (idempotent skip).
        assert result is None
        # Critical: NO write occurred to the managed path.
        assert write_calls == [], (
            f"Expected zero writes when content changed under the writer, "
            f"but {len(write_calls)} write(s) occurred. The inside-lock "
            f"re-read guard is not protecting the concurrent writer's content."
        )
        # Both reads happened (outer + inner re-read), confirming the lock
        # path was actually entered.
        assert call_state["count"] >= 2, (
            f"Expected at least 2 reads of the managed path (outer + "
            f"inner re-read), got {call_state['count']}. The inside-lock "
            f"re-read may have been skipped."
        )

    def test_modified_write_succeeds_when_no_concurrent_change(self, tmp_path):
        """Sanity check: when no concurrent change happens, the function
        DOES write the modified content (stale marker insertion).

        This is the positive complement to the concurrent-change test —
        without it, a future bug that disables ALL writes would silently
        pass the concurrent-change test.
        """
        from session_init import check_pinned_staleness

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(self._stale_pinned_content(), encoding="utf-8")

        with patch("session_init._get_project_claude_md_path", return_value=claude_md), \
             patch("staleness._get_project_claude_md_path", return_value=claude_md):
            result = check_pinned_staleness()

        # Function reports the stale entry was found
        assert result is not None
        assert "stale pin" in result.lower()

        # The file was actually modified — STALE marker is now present
        post_content = claude_md.read_text(encoding="utf-8")
        assert "<!-- STALE: Last relevant" in post_content
        assert self.STALE_DATE in post_content


class TestParsePinnedSectionTerminator:
    """Regression guard: `_parse_pinned_section` must terminate on PACT
    boundary markers and H2 headings.

    Round 10 simplified these tests: fence-awareness tests are deleted
    because the parser now operates within the PACT-managed region only
    (no user-authored fenced code blocks). The unfenced terminator test
    remains as a regression guard for the basic termination contract.
    """

    def test_unfenced_pact_marker_still_terminates_pinned_section(self):
        """PACT boundary markers terminate the pinned section scan."""
        from staleness import _parse_pinned_section

        content = (
            "# Project Memory\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "### Regular pin\n"
            "No fences here.\n"
            "\n"
            "<!-- PACT_MEMORY_END -->\n"
            "## Working Memory\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _pinned_start, pinned_end, pinned_content = result

        assert "Regular pin" in pinned_content
        # The marker is the terminator — pinned_content must NOT contain it
        assert "PACT_MEMORY_END" not in pinned_content
        assert content[pinned_end:].lstrip().startswith("<!-- PACT_MEMORY_END -->")

class TestParsePinnedSectionManagedRegionBounding:
    """Round 10: _parse_pinned_section bounds its search to the managed region
    and returns full-file positions for write-back.
    """

    def test_returns_full_file_offsets_with_managed_region(self):
        """Positions in the returned tuple must be absolute in the full
        content, not relative to the managed region.
        """
        from staleness import _parse_pinned_section
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            MANAGED_END_MARKER,
        )

        preamble = "user notes above\n\n"
        managed_body = (
            "## Pinned Context\n"
            "\n"
            "### Real pin\n"
            "Pin content here.\n"
            "\n"
        )

        content = (
            preamble
            + MANAGED_START_MARKER + "\n"
            + managed_body
            + MANAGED_END_MARKER + "\n"
            + "user notes below\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        pinned_start, pinned_end, pinned_content = result

        # Offsets must be in the full content, not managed-region-relative
        assert pinned_start > len(preamble), (
            "pinned_start must be an absolute offset past the preamble"
        )
        assert content[pinned_start:pinned_start + 3] == "\n##" or "### " in content[pinned_start:pinned_start + 20]
        assert "Pin content here." in pinned_content

    def test_fallback_to_full_content_without_managed_markers(self):
        """Pre-migration file: no managed markers, parser scans full content."""
        from staleness import _parse_pinned_section

        content = (
            "# Project Memory\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "### Old pin\n"
            "Old content.\n"
            "\n"
            "## Working Memory\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _, _, pinned_content = result
        assert "Old content." in pinned_content


class TestPinCapsTwinCopyDrift:
    """Drift detection for pin_caps constants twin-copied into working_memory.

    pin_caps.py lives under pact-plugin/hooks/ and working_memory.py lives
    under pact-plugin/skills/pact-memory/scripts/ — separate package
    boundary means direct import is not available. Constants MUST stay in
    sync; this test fails loudly when they drift.
    """

    def test_pin_count_cap_twins_match(self):
        import pin_caps
        from scripts import working_memory

        assert pin_caps.PIN_COUNT_CAP == working_memory.PIN_COUNT_CAP, (
            "PIN_COUNT_CAP drift between hooks/pin_caps.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the same commit"
        )

    def test_pin_size_cap_twins_match(self):
        import pin_caps
        from scripts import working_memory

        assert pin_caps.PIN_SIZE_CAP == working_memory.PIN_SIZE_CAP, (
            "PIN_SIZE_CAP drift between hooks/pin_caps.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the same commit"
        )

    def test_pin_stale_block_threshold_twins_match(self):
        import pin_caps
        from scripts import working_memory

        assert pin_caps.PIN_STALE_BLOCK_THRESHOLD == working_memory.PIN_STALE_BLOCK_THRESHOLD, (
            "PIN_STALE_BLOCK_THRESHOLD drift between hooks/pin_caps.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the same commit"
        )

    def test_override_rationale_max_twins_match(self):
        import pin_caps
        from scripts import working_memory

        assert pin_caps.OVERRIDE_RATIONALE_MAX == working_memory.OVERRIDE_RATIONALE_MAX, (
            "OVERRIDE_RATIONALE_MAX drift between hooks/pin_caps.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the same commit"
        )

    def test_forbidden_line_terminator_chars_twins_match(self):
        """Line-terminator char set MUST agree across parser and hook.

        Cycle-7 introduced a CLI-side refusal for line terminators in
        override rationales. Cycle-8 demoted the CLI to advisory-only;
        rationale validation moved to the PreToolUse hook
        (hooks/pin_caps_gate.py). The CLI no longer carries its own
        forbidden-char set, so this test compares parser ↔ hook only.

        Drift guard: the hook DERIVES its forbidden-char set from
        `pin_caps._FORBIDDEN_TERMINATOR_TABLE` at module load (single
        source of truth). In a correctly wired repo the equality always
        holds because the hook's set is literally `chr(k) for k in the
        parser table`. This test catches the regression of someone
        reverting the derivation to a hand-maintained literal — if that
        happens, drift is possible, and this assertion fires with both
        sides enumerated.
        """
        import pin_caps
        import pin_caps_gate

        parser_chars = set(chr(k) for k in pin_caps._FORBIDDEN_TERMINATOR_TABLE.keys())
        hook_chars = set(pin_caps_gate._FORBIDDEN_RATIONALE_CHARS)

        assert parser_chars == hook_chars, (
            "Line-terminator char-set drift between parser and hook:\n"
            f"  pin_caps._FORBIDDEN_TERMINATOR_TABLE (parser):   {sorted(parser_chars)!r}\n"
            f"  pin_caps_gate._FORBIDDEN_RATIONALE_CHARS (hook): {sorted(hook_chars)!r}\n"
            "The hook SHOULD derive from the parser table at module load "
            "(see pin_caps_gate.py) so it cannot drift. If you see this "
            "failure, someone reverted the derivation to a hand-maintained "
            "literal. Fix by restoring the `chr(k) for k in "
            "pin_caps._FORBIDDEN_TERMINATOR_TABLE.keys()` form."
        )


# Chars per word in real pinned prose. FROZEN, not measured at test time: the
# corpus it describes is a user's CLAUDE.md, which is gitignored and absent from
# a fresh clone, so measuring it here would make this file pass locally and
# error in CI -- a test that fails for a reason unrelated to its invariant
# teaches people to ignore it.
#
# HOW IT WAS OBTAINED, so it can be re-derived rather than trusted: measured
# over the STRIPPED body text, which is what pin_caps._extract_body_chars
# returns after removing the per-line date comment and the STALE marker, across
# a real twelve-pin document. 12,306 chars over 1,757 words.
#
# The value is not the fragile part; the POPULATION is. Re-derive it the same
# way. test_measurement_method_matches_the_enforced_population below pins the
# method, so a later re-derivation cannot quietly use a different set of bytes.
_PINNED_PROSE_CHARS_PER_WORD = 7.004

# Heading line plus date-comment line, in words, for ONE pin. Expressed per-pin
# because that is what it is: a flat total would be correct only at the pin
# count it happened to be authored against, and would drift silently from every
# other count while the test stayed green.
_PIN_CHROME_WORDS = 32

# Where the advisory must sit inside the space the caps allow.
_ADVISORY_FLOOR_FRACTION = 0.5


class TestPinnedBudgetAgainstCapsCoherence:
    """The advisory budget and the enforcement caps must not contradict.

    PINNED_CONTEXT_TOKEN_BUDGET advises a user to archive. PIN_COUNT_CAP and
    PIN_SIZE_CAP refuse an edit. Nothing in the repo related the two, and they
    did contradict: a budget of 1200 against a twelve-by-1500 cap meant a
    document FULLY LEGAL under the caps exceeded the advisory by more than
    twice, so the advisory fired on documents it had no business flagging and
    could not be satisfied without deleting legal content.

    THE RELATION IS PINNED HERE, NOT THE VALUE. Deriving the budget from the
    caps was considered and declined: a budget at or above the derived cost can
    only fire once a document is at full legal capacity, which is the point
    where the caps are already refusing edits and the advice arrives at the
    wall. Advising earlier requires a FRACTION of the derived cost, so a free
    coefficient is not removed by deriving -- it only moves.

    WHAT THIS DESIGN ACHIEVES, which is less than eliminating the free number
    and is the most that was available: the coefficient sits over a quantity
    computed from the caps rather than over a measured document, so it cannot
    go stale as prose changes; and the band is wide enough that being wrong
    about the coefficient by a tenth changes no verdict.
    """

    def _derived_ceiling(self):
        """Estimated tokens of a document sitting at full legal capacity.

        Routed through the shipped `estimate_tokens` rather than repeating its
        coefficient, so the ceiling follows the estimator if the estimator
        changes.
        """
        import pin_caps
        from staleness import estimate_tokens

        body_words = (
            pin_caps.PIN_COUNT_CAP
            * pin_caps.PIN_SIZE_CAP
            / _PINNED_PROSE_CHARS_PER_WORD
        )
        chrome_words = pin_caps.PIN_COUNT_CAP * _PIN_CHROME_WORDS
        # round, not int: truncating the word count before the estimator
        # truncates a second time and lands two tokens below the figure every
        # written record of this decision carries.
        return estimate_tokens("w " * round(body_words + chrome_words))

    def test_budget_is_below_the_cost_of_a_maximally_legal_document(self):
        """Above this, the advisory can never fire before the caps bind."""
        from staleness import PINNED_CONTEXT_TOKEN_BUDGET

        ceiling = self._derived_ceiling()
        assert PINNED_CONTEXT_TOKEN_BUDGET < ceiling, (
            f"PINNED_CONTEXT_TOKEN_BUDGET ({PINNED_CONTEXT_TOKEN_BUDGET}) is at "
            f"or above the cost of a document at full legal capacity "
            f"({ceiling}). The advisory could then only fire once PIN_COUNT_CAP "
            f"and PIN_SIZE_CAP are already refusing edits. Lower the budget, or "
            f"revisit the caps."
        )

    def test_budget_is_above_the_advisory_floor(self):
        """Below this, the advisory fires on documents that are merely healthy."""
        from staleness import PINNED_CONTEXT_TOKEN_BUDGET

        floor = _ADVISORY_FLOOR_FRACTION * self._derived_ceiling()
        assert PINNED_CONTEXT_TOKEN_BUDGET > floor, (
            f"PINNED_CONTEXT_TOKEN_BUDGET ({PINNED_CONTEXT_TOKEN_BUDGET}) is at "
            f"or below {floor:.0f}, so it flags documents well inside what the "
            f"caps permit. If PIN_COUNT_CAP or PIN_SIZE_CAP moved, the budget "
            f"needs revisiting in the same commit."
        )

    def test_the_band_rejects_the_value_it_was_written_to_catch(self):
        """Non-vacuity, carried in the suite rather than run once by hand.

        The historical budget of 1200 is the incoherence this guard exists to
        refuse. If a future edit widens the band until 1200 sits inside it, the
        guard still passes its other two arms while having stopped guarding.
        """
        historical_budget = 1200
        ceiling = self._derived_ceiling()
        floor = _ADVISORY_FLOOR_FRACTION * ceiling
        assert not (floor < historical_budget < ceiling), (
            f"the band ({floor:.0f}, {ceiling}) now ACCEPTS the historical 1200, "
            f"so it no longer detects the contradiction it was written for"
        )

    def test_measurement_method_matches_the_enforced_population(self):
        """The conversion figure must describe the bytes the cap counts.

        `_PINNED_PROSE_CHARS_PER_WORD` converts a CHAR cap into the WORD unit
        the budget is expressed in. That conversion means something only if the
        text it was measured over is the same text PIN_SIZE_CAP is enforced
        against. This asserts the reconstruction used to obtain it against the
        value the shipped parser reports, for every pin of a synthetic
        document.

        Stated without a named failure mode on purpose: measure over the
        population the constant is defined on, and assert the reconstruction
        against the shipped value.
        """
        import pin_caps

        pins_text = ""
        for i in range(pin_caps.PIN_COUNT_CAP):
            # Exercise both strip paths the enforced value applies: the longer
            # reconfirmed date-comment spelling, and a STALE marker.
            if i % 3 == 0:
                comment = (
                    f"<!-- pinned: 2026-01-0{i % 9 + 1}, reconfirmed: 2026-02-01 "
                    f"because it still answers a live question -->"
                )
            else:
                comment = f"<!-- pinned: 2026-01-0{i % 9 + 1} -->"
            stale = "<!-- STALE: Last relevant 2026-01-01 -->" if i % 4 == 0 else ""
            pins_text += (
                f"{comment}\n### Pin {i}\n{stale}\nbody words for pin {i} here\n\n"
            )

        pins = pin_caps.parse_pins(pins_text)
        assert len(pins) == pin_caps.PIN_COUNT_CAP, (
            "fixture did not parse into the expected number of pins; the "
            "control cannot certify a population it did not build"
        )

        for pin in pins:
            rebuilt = "\n".join(
                pin_caps._DATE_COMMENT_RE.sub("", line)
                for line in pin.body.splitlines()
            )
            rebuilt = pin_caps._STALE_MARKER_RE.sub("", rebuilt).strip()
            assert len(rebuilt) == pin.body_chars, (
                f"the reconstruction used to measure "
                f"_PINNED_PROSE_CHARS_PER_WORD counts a different set of bytes "
                f"than pin_caps reports for {pin.heading!r}: rebuilt "
                f"{len(rebuilt)} vs body_chars {pin.body_chars}"
            )


class TestFileLockTwinCopyDrift:
    """Drift detection for the file_lock twin vendored into working_memory.

    file_lock is twin-copied from hooks/shared/claude_md_manager into
    skills/pact-memory/scripts/working_memory (skills/ cannot import from
    hooks/shared/). The function BODY must stay byte-identical so the skill
    serializes against the hook on the same sidecar inode; the docstring may
    differ. This fails loudly when the executable logic drifts.
    """

    @staticmethod
    def _extract_body(source: str) -> str:
        """Return the executable body, stripping decorators, the def line,
        and a leading docstring; then dedent + normalize.

        Unlike TestEstimateTokensEquivalence._extract_body, file_lock carries a
        @contextmanager decorator, so this skips ALL leading ``@`` lines before
        dropping the def line. This makes the comparison docstring-tolerant
        (the twin carries a shorter docstring) while pinning the logic.
        """
        lines = source.split("\n")
        idx = 0
        while idx < len(lines) and lines[idx].lstrip().startswith("@"):
            idx += 1
        # lines[idx] is the def line; drop it too.
        body_lines = lines[idx + 1:]
        body_text = textwrap.dedent("\n".join(body_lines)).strip()
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break
        return body_text

    def test_file_lock_bodies_are_identical(self):
        """The file_lock body MUST be byte-identical across the two copies.

        Cross-process serialization correctness depends on the twin behaving
        exactly like the canonical lock (same sidecar path, same fcntl flock
        semantics, same timeout/poll loop). Compares logic only — docstrings
        are allowed to differ.
        """
        from shared.claude_md_manager import file_lock as canonical
        from scripts.working_memory import file_lock as twin

        canonical_body = self._extract_body(inspect.getsource(canonical))
        twin_body = self._extract_body(inspect.getsource(twin))

        assert canonical_body == twin_body, (
            "file_lock twin drift between hooks/shared/claude_md_manager.py "
            "and skills/pact-memory/scripts/working_memory.py — update both "
            "in the SAME commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )

    def test_lock_timeout_constants_match(self):
        """The two lock-tuning constants are part of the twin and must match."""
        import shared.claude_md_manager as cmm
        from scripts import working_memory as wm

        assert cmm._LOCK_TIMEOUT_SECONDS == wm._LOCK_TIMEOUT_SECONDS, (
            "_LOCK_TIMEOUT_SECONDS drift between claude_md_manager.py and "
            "working_memory.py — update both in the same commit"
        )
        assert cmm._LOCK_POLL_INTERVAL == wm._LOCK_POLL_INTERVAL, (
            "_LOCK_POLL_INTERVAL drift between claude_md_manager.py and "
            "working_memory.py — update both in the same commit"
        )


class TestSanitizePromptFieldTwinCopyDrift:
    """Drift detection for the _sanitize_prompt_field twin in working_memory.

    _sanitize_prompt_field is twin-copied from hooks/shared/session_resume
    into skills/pact-memory/scripts/working_memory (skills/ cannot import
    from hooks/shared/). The function BODY must stay byte-identical so both
    the hook write path and the skill write path collapse the SAME control
    characters at the SAME bound; the docstring may differ.

    NO OTHER TEST COVERS THIS PAIR, WHICH IS WHY THE CLASS EXISTS RATHER
    THAN A CASE ADDED ELSEWHERE. The neighbouring twin gates import
    _atomic_write_text and file_lock, so a divergence in the SANITIZER
    reddens none of them: without this class a one-copy edit is green and
    silent, and the two write paths would disagree about what a field value
    can carry into CLAUDE.md.
    """

    @staticmethod
    def _extract_body(func) -> str:
        """Return the executable body, dropping the signature and docstring.

        Unlike the two extractors above, this one parses rather than counts
        lines. _sanitize_prompt_field carries a MULTI-LINE signature, so
        dropping a fixed number of leading lines would leave parameter lines
        in the "body" and compare the signature as if it were logic.
        """
        source = textwrap.dedent(inspect.getsource(func))
        statements = ast.parse(source).body[0].body
        first = statements[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            statements = statements[1:]
        body_start = statements[0].lineno - 1
        return textwrap.dedent(
            "\n".join(source.split("\n")[body_start:])
        ).strip()

    @staticmethod
    def _extract_signature(func):
        """Return an ast-NORMALISED signature, for comparison across twins.

        WHY A NORMALISED FORM AND NOT THE SOURCE LINES. The extractor above
        DROPS the signature, on purpose, so the body comparison does not
        compare parameter lines as if they were logic. That leaves the
        signature compared by nobody, which is the gap this closes. Comparing
        the raw source lines instead would go red when somebody re-wraps a
        multi-line signature onto one line, which changes no behaviour.

        THE COMPARED SURFACE, so a later reader can reproduce it: the ORDERED
        parameter names, the `ast.unparse` of each annotation, and the
        `ast.unparse` of each default EXPRESSION.

        SOURCE FORM, NOT RESOLVED VALUE, AND THAT IS THE POINT. The default
        `limit` reads `_REFRESH_FIELD_TRUNCATION_LIMIT` in the two copies. A
        copy rewritten to spell the bare literal 200 has the SAME resolved
        value today, so a value comparison stays green while the two copies
        stop tracking one constant. The unparse of the expression separates
        them.
        """
        args = ast.parse(textwrap.dedent(inspect.getsource(func))).body[0].args

        positional = args.posonlyargs + args.args
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)

        rendered = [
            (a.arg,
             ast.unparse(a.annotation) if a.annotation else None,
             ast.unparse(d) if d is not None else None)
            for a, d in zip(positional, defaults)
        ]
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            rendered.append(
                ("*" + a.arg,
                 ast.unparse(a.annotation) if a.annotation else None,
                 ast.unparse(d) if d is not None else None)
            )
        for star, name in (("*", args.vararg), ("**", args.kwarg)):
            if name is not None:
                rendered.append(
                    (star + name.arg,
                     ast.unparse(name.annotation) if name.annotation else None,
                     None)
                )
        return tuple(rendered)

    def test_sanitize_prompt_field_bodies_are_identical(self):
        """The _sanitize_prompt_field body MUST be byte-identical across twins.

        The body decides which characters can reach a line of CLAUDE.md and
        at what length. A divergence would let one write path admit a
        character the other collapses — so a heading injected through the
        skill path would be stripped through the hook path, or the reverse,
        and the guard would hold on only one of the two writers.
        """
        from shared.session_resume import _sanitize_prompt_field as canonical
        from scripts.working_memory import _sanitize_prompt_field as twin

        canonical_body = self._extract_body(canonical)
        twin_body = self._extract_body(twin)

        assert canonical_body == twin_body, (
            "_sanitize_prompt_field twin drift between "
            "hooks/shared/session_resume.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the SAME commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )

    def test_sanitize_prompt_field_signatures_are_identical(self):
        """The twin SIGNATURE must agree, in source form, across the copies.

        THE BODY ARM ABOVE CANNOT SEE THIS, BY CONSTRUCTION. `_extract_body`
        drops the signature so it does not compare parameter lines as logic,
        and the constants arm below compares MODULE-LEVEL names. A default
        rewritten in ONE copy sits in neither population: the bodies stay
        byte-identical, the four module constants stay equal, and the two
        write paths bound a field differently with no arm red.

        MEASURED: the two copies take `limit` defaulted to
        `_REFRESH_FIELD_TRUNCATION_LIMIT`, which is 200. Rewriting one copy
        to the bare literal 200 changes NO behaviour today and breaks the
        coupling, so the next move of that constant bounds one writer and not
        the other.

        WHAT THIS ARM DOES NOT ASSERT, STATED SO NOBODY READS IT WIDER. It
        compares the signature SURFACE, not the resolved default. Two copies
        that name DIFFERENT constants of equal value agree here and are
        caught by the constants arm below. The two arms are complementary,
        and neither one covers the other.
        """
        from shared.session_resume import _sanitize_prompt_field as canonical
        from scripts.working_memory import _sanitize_prompt_field as twin

        canonical_signature = self._extract_signature(canonical)
        twin_signature = self._extract_signature(twin)

        # NON-VACUITY: an extractor that returned an empty tuple for each side
        # would compare equal and report a clean sweep. The function takes a
        # value and a bound, so the surface cannot be empty.
        assert len(canonical_signature) >= 2, (
            f"the canonical signature parsed as {canonical_signature!r}, which "
            "is too small to be this function. The extractor is broken, and an "
            "empty surface makes the comparison below pass for the wrong cause."
        )

        assert canonical_signature == twin_signature, (
            "_sanitize_prompt_field SIGNATURE drift between "
            "hooks/shared/session_resume.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the SAME commit.\n"
            "Each row is (parameter, annotation, default), in source form.\n"
            f"canonical: {canonical_signature}\n"
            f"twin:      {twin_signature}"
        )

    def test_sanitize_prompt_field_constants_match(self):
        """The sanitizer constants are part of the twin and must match.

        The drift gate above compares the function body only, and the body
        names these rather than spelling their values, so a change to one
        copy of a constant leaves both bodies identical while the two write
        paths bound differently. This arm is what makes that visible.

        THE IDENTIFIER BOUND IS HELD HERE EVEN THOUGH session_resume TAKES
        NO IDENTIFIER VALUE TODAY. The FIELD-KIND CLASSIFICATION is shared
        vocabulary: a field kind that lives in one copy alone lets the two
        writers disagree about what a kind means, and the defect this bound
        repairs was a field with NO row in the classification falling to
        the widest default.
        """
        import shared.session_resume as sr
        from scripts import working_memory as wm

        assert sr._REFRESH_FIELD_TRUNCATION_LIMIT == wm._REFRESH_FIELD_TRUNCATION_LIMIT, (
            "_REFRESH_FIELD_TRUNCATION_LIMIT drift between session_resume.py "
            "and working_memory.py — update both in the same commit"
        )
        assert sr._REFRESH_PATH_TRUNCATION_LIMIT == wm._REFRESH_PATH_TRUNCATION_LIMIT, (
            "_REFRESH_PATH_TRUNCATION_LIMIT drift between session_resume.py "
            "and working_memory.py — update both in the same commit"
        )
        assert (
            sr._REFRESH_IDENTIFIER_TRUNCATION_LIMIT
            == wm._REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        ), (
            "_REFRESH_IDENTIFIER_TRUNCATION_LIMIT drift between "
            "session_resume.py and working_memory.py — update both in the "
            "same commit"
        )
        assert sr._PROMPT_CONTROL_CHARS_RE.pattern == wm._PROMPT_CONTROL_CHARS_RE.pattern, (
            "_PROMPT_CONTROL_CHARS_RE drift between session_resume.py and "
            "working_memory.py — update both in the same commit"
        )

    def test_the_compared_set_covers_every_shared_constant(self):
        """The arm above must compare EACH constant the two copies share.

        THE ARM ABOVE NAMES FOUR CONSTANTS, SO IT CANNOT NOTICE A FIFTH. A
        shared value added to the two copies tomorrow leaves that arm GREEN
        and silent about the new value, and the two write paths can then
        bound differently through a constant nobody compares.

        MEASURED WHEN THIS ARM WAS WRITTEN: shared 4, compared 4, shared
        and not compared 0. So the arm above is COMPLETE, and it is complete
        by the care of the author rather than by construction. This test is
        what makes it complete by construction.

        COUNTING RULE, STATED SO A LATER READER CAN REPRODUCE THE POPULATION
        RATHER THAN TRUST IT: module-level `NAME = value` assignments, parsed
        with `ast` from each file, intersected BY NAME. Assignments nested in
        a function, a class or a conditional are OUT of the population,
        because a module-level constant is the shape the twin uses.

        WHEN THIS GOES RED, DECIDE WHICH KIND OF NAME APPEARED, BECAUSE THE
        TWO KINDS TAKE OPPOSITE ACTIONS.

        1. THE NEW SHARED NAME IS PART OF THE TWINNED SANITIZER SURFACE. Add
           it to the arm above, in the commit that introduces it.
        2. THE NEW SHARED NAME IS A COINCIDENCE OF SPELLING with no bearing
           on `_sanitize_prompt_field`. It does NOT belong in an arm that
           byte-compares the constants of ONE function, so exclude it here
           and state its cause at the exclusion.

        CASE 2 IS REACHABLE RATHER THAN THEORETICAL, and the population is
        why: this arm intersects EVERY module-level name the two files
        share, which is WIDER than the sanitizer surface. Measured when this
        arm was written: `working_memory.py` defines `logger` at module level
        and `session_resume.py` does not. One ordinary edit that adds a
        module logger to the second file puts `logger` in this intersection,
        and a reader who obeys case 1 without thought would then add it to a
        byte-comparison arm, which starts to assert a relation nobody meant
        to hold.

        IN NO CASE WIDEN THIS TEST INTO A BLANKET ACCEPT. An exclusion names
        ONE value and carries its cause. A blanket accept returns the gate to
        a promise somebody must remember to keep.
        """
        import ast as ast_module
        import pathlib

        plugin_root = pathlib.Path(__file__).resolve().parent.parent

        def module_level_names(relative_path):
            tree = ast_module.parse((plugin_root / relative_path).read_text())
            return {
                node.targets[0].id
                for node in tree.body
                if isinstance(node, ast_module.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast_module.Name)
            }

        canonical = module_level_names("hooks/shared/session_resume.py")
        twin = module_level_names("skills/pact-memory/scripts/working_memory.py")
        shared = canonical & twin

        # The names the arm above compares, written out rather than derived,
        # because deriving them from the same source they guard would make
        # this assertion true by construction and prove nothing.
        compared = {
            "_REFRESH_FIELD_TRUNCATION_LIMIT",
            "_REFRESH_PATH_TRUNCATION_LIMIT",
            "_REFRESH_IDENTIFIER_TRUNCATION_LIMIT",
            "_PROMPT_CONTROL_CHARS_RE",
        }

        # NON-VACUITY: the parse must find the two populations. An empty set
        # on either side makes the comparison below pass for the wrong cause.
        assert canonical, "no module-level names parsed from session_resume.py"
        assert twin, "no module-level names parsed from working_memory.py"

        assert shared == compared, (
            "the set of constants SHARED by the two _sanitize_prompt_field "
            "copies no longer equals the set the drift arm compares.\n"
            f"  shared and not compared: {sorted(shared - compared)}\n"
            f"  compared and not shared: {sorted(compared - shared)}\n"
            "Add each shared name to test_sanitize_prompt_field_constants_match "
            "in the same commit that introduces it."
        )


class TestStalenessLexicalBaseParity:
    """#1247: the lexical base recovered from a SUPPLIED path (option D in
    check_pinned_staleness, via staleness._lexical_base_of) MUST equal the base
    the resolver itself used. session_init passes staleness a resolved PATH
    (not a base), so the containment anchor is recovered by inverting the
    resolver's construction; if _resolve_project_claude_md_with_base ever grows
    a THIRD path shape, that lexical formula would silently diverge and the
    guard would anchor on the wrong root. This test turns that divergence into
    a RED -- the single-source-of-truth safeguard the architect mandated
    (twin-drift discipline applied to anchor derivation). It exercises the REAL
    production formula (_lexical_base_of), not a test copy.
    """

    def test_lexical_base_matches_resolver_base_dot_claude(self, tmp_path, monkeypatch):
        import staleness as st

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text("# x\n", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        path, base = st._resolve_project_claude_md_with_base()
        assert path is not None and base is not None
        assert st._lexical_base_of(path) == base, (
            "lexical base recovery diverged from the resolver's base for the "
            ".claude/CLAUDE.md shape — option D would anchor containment on the "
            "wrong root"
        )

    def test_lexical_base_matches_resolver_base_legacy(self, tmp_path, monkeypatch):
        import staleness as st

        (tmp_path / "CLAUDE.md").write_text("# x\n", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        path, base = st._resolve_project_claude_md_with_base()
        assert path is not None and base is not None
        assert st._lexical_base_of(path) == base, (
            "lexical base recovery diverged from the resolver's base for the "
            "legacy ./CLAUDE.md shape — option D would anchor containment on "
            "the wrong root"
        )


class TestAtomicWriteTwinCopyDrift:
    """Drift detection for the _atomic_write_text twin (#1247).

    _atomic_write_text is twin-copied from hooks/shared/claude_md_manager into
    skills/pact-memory/scripts/working_memory (skills/ cannot import from
    hooks/shared/). Since #1247 it carries the CONTAINMENT SECURITY CHECK, so
    the function BODY must stay byte-identical across the two copies -- a
    silent divergence in the security check between the hook and skill copies
    is the #1118-class hazard this gate exists to prevent. The docstring may
    differ (each copy points at the other); the executable logic may not.
    """

    @staticmethod
    def _extract_body(source: str) -> str:
        """Return the executable body: skip leading decorators + the def line,
        strip a leading docstring, dedent, normalize. Same extractor as
        TestFileLockTwinCopyDrift (docstring-tolerant, logic-pinning)."""
        lines = source.split("\n")
        idx = 0
        while idx < len(lines) and lines[idx].lstrip().startswith("@"):
            idx += 1
        body_lines = lines[idx + 1:]
        body_text = textwrap.dedent("\n".join(body_lines)).strip()
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break
        return body_text

    def test_atomic_write_text_bodies_are_identical(self):
        """The _atomic_write_text body MUST be byte-identical across the twins.

        The body carries the #1247 containment check (kernel object ancestry on
        a pinned directory descriptor, fail-closed); a divergence would let the
        hook and skill write paths enforce different containment, silently
        defeating the guard on one side. Compares logic only -- docstrings are
        allowed to differ.

        THIS GATE PROVES BYTE-IDENTITY, NOT THAT EITHER COPY IS EXERCISED. It
        goes red whenever the bodies diverge for ANY reason, including a
        deliberate single-twin experiment -- so a red here is not by itself
        evidence of a behavioural regression, and counting it as one inflates
        the apparent coverage of whichever twin was left untouched. The
        behavioural coverage of each copy lives in
        test_containment_certification.py, which drives both independently.
        """
        from shared.claude_md_manager import _atomic_write_text as canonical
        from scripts.working_memory import _atomic_write_text as twin

        canonical_body = self._extract_body(inspect.getsource(canonical))
        twin_body = self._extract_body(inspect.getsource(twin))

        assert canonical_body == twin_body, (
            "_atomic_write_text twin drift between "
            "hooks/shared/claude_md_manager.py and "
            "skills/pact-memory/scripts/working_memory.py — the #1247 "
            "containment check must stay identical; update both in the SAME "
            "commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )


class TestLineEndingHelperTwinCopyDrift:
    """Drift detection for the two line-ending helpers.

    THIS GATE ESTABLISHES THE IDENTITY RATHER THAN PRESERVING IT, AND THAT IS
    WHY IT IS NOT OPTIONAL. Its siblings guard functions that were COPIED, so
    the two sides started identical and the gate reports a later divergence.
    `_detect_line_ending` was REWRITTEN when the restore moved into the write
    seam: it takes a directory descriptor and a leaf name now, because a read by
    NAME inside `_atomic_write_text` would reintroduce the race that function's
    descriptor design removes. A rewrite has no shared ancestor, so nothing
    except this gate makes the two copies agree at all.

    WHY THE TWO COPIES ARE THERE. `skills/pact-memory/scripts/` cannot import
    from `hooks/shared/`, the same package boundary that produced the `file_lock`
    and `_atomic_write_text` twins.

    WHAT DIVERGENCE COSTS. The two helpers decide the BYTES that land in a
    user's CLAUDE.md. A hook copy and a skill copy that disagree write the same
    document with different line endings, so two writers fight over the file and
    each one reports a whole-file change to the user. The docstrings may differ,
    because each copy points at the other. The logic may not.
    """

    @staticmethod
    def _extract_body(source: str) -> str:
        """Return the executable body: skip leading decorators + the def line,
        strip a leading docstring, dedent, normalize. Same extractor as
        TestAtomicWriteTwinCopyDrift (docstring-tolerant, logic-pinning)."""
        lines = source.split("\n")
        idx = 0
        while idx < len(lines) and lines[idx].lstrip().startswith("@"):
            idx += 1
        body_lines = lines[idx + 1:]
        body_text = textwrap.dedent("\n".join(body_lines)).strip()
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break
        return body_text

    @pytest.mark.parametrize(
        "helper", ["_detect_line_ending", "_restore_line_ending"]
    )
    def test_line_ending_helper_bodies_are_identical(self, helper):
        """Each helper's body MUST be byte-identical across the twins."""
        import shared.claude_md_manager as canonical_mod
        from scripts import working_memory as twin_mod

        canonical_body = self._extract_body(
            inspect.getsource(getattr(canonical_mod, helper))
        )
        twin_body = self._extract_body(
            inspect.getsource(getattr(twin_mod, helper))
        )

        assert canonical_body == twin_body, (
            f"{helper} twin drift between "
            f"hooks/shared/claude_md_manager.py and "
            f"skills/pact-memory/scripts/working_memory.py. The two decide the "
            f"bytes that land in a user's CLAUDE.md, so a divergence makes the "
            f"hook and the skill write one file with different line endings. "
            f"Update both in the SAME commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )

    def test_the_gate_can_tell_the_two_helpers_apart(self):
        """NON-VACUITY. An extractor that returned the same text for everything
        would satisfy the assertion above forever, and it would satisfy it
        LOUDEST in the state this gate exists to catch: two copies that share
        nothing. Prove the extractor separates two functions that genuinely
        differ before trusting it to report that two agree."""
        from scripts import working_memory as twin_mod

        detect = self._extract_body(
            inspect.getsource(twin_mod._detect_line_ending)
        )
        restore = self._extract_body(
            inspect.getsource(twin_mod._restore_line_ending)
        )

        assert detect and restore, "the extractor returned an EMPTY body"
        assert detect != restore, (
            "the extractor cannot distinguish two different helpers, so the "
            "drift assertions above prove nothing"
        )


class TestRestoreHasOneOwnerPerTwin:
    """The line-ending restore has ONE owner for each twin, and it is the seam.

    WHAT THIS CONVERTS. Before the restore moved into `_atomic_write_text`, one
    call site did it for itself and the other nine did not. A branch written
    against that shape carries its own call-site restore, so a later merge can
    land the seam restore AND a call-site restore in one tree. The substitution
    then runs two times.

    WHY THAT IS A CORRUPTION AND NOT AN INEFFICIENCY. `_restore_line_ending`
    normalises before it applies, so a second application is a no-op TODAY. That
    makes the two-owner state SILENT, which is the reason it wants a mechanical
    gate rather than a note: nothing in the output reports it, and the next
    editor of the helper can remove the normalise step without knowing a second
    owner is relying on it.

    FAILURE DIRECTION, and it is fail-safe. This arm reddens when a SECOND owner
    appears anywhere in the shipped tree. A future writer with a legitimate
    reason to restore for itself turns it red and must state the reason in the
    same commit. That is the intended cost.
    """

    # COUNT RULE, STATED BESIDE THE NUMBER: one entry for each CALL EXPRESSION
    # of the restore helper found by an AST walk of every `.py` file under
    # `pact-plugin/hooks/` and `pact-plugin/skills/`. Tests are not in that
    # population, and a mention inside a comment or a string is not a call.
    _SEARCH_ROOTS = ("hooks", "skills")
    _RESTORE = "_restore_line_ending"

    @staticmethod
    def _calls_with_enclosing_function(tree, name):
        """Return the enclosing function name for each call of `name`.

        Attributes a call to its INNERMOST enclosing function, and to None at
        module level, so a restore moved out of a function reddens rather than
        being credited to whatever function sits above it.
        """
        found = []

        def visit(node, enclosing):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child.name)
                    continue
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == name
                ):
                    found.append(enclosing)
                visit(child, enclosing)

        visit(tree, None)
        return found

    def _sites(self):
        """DISCOVER the call sites. Do NOT enumerate them.

        A guard whose target list is written by hand passes green for anything
        it forgot to name, which is the failure this walk exists to avoid.
        """
        plugin_root = Path(__file__).parent.parent
        sites = {}
        scanned = 0
        for root in self._SEARCH_ROOTS:
            for path in sorted((plugin_root / root).rglob("*.py")):
                scanned += 1
                tree = ast.parse(path.read_text(encoding="utf-8"))
                enclosing = self._calls_with_enclosing_function(
                    tree, self._RESTORE
                )
                if enclosing:
                    sites[path.relative_to(plugin_root).as_posix()] = enclosing
        return sites, scanned

    def test_the_only_owner_of_the_restore_is_the_write_seam(self):
        sites, scanned = self._sites()

        # NON-VACUITY FIRST, AND IT IS REQUIRED. A walk that found no call at
        # all satisfies an "every call is in the seam" assertion perfectly, so
        # the assertion below is evidence only once the population is known to
        # be non-empty. A broken path or a renamed helper lands here.
        assert scanned > 0, "the walk found no Python file at all"
        assert sites, (
            f"no call of {self._RESTORE!r} was found in "
            f"{list(self._SEARCH_ROOTS)} across {scanned} files. Either the "
            f"seam no longer restores the line ending, or the helper was "
            f"renamed and this guard is now measuring nothing"
        )

        owners = {name for names in sites.values() for name in names}
        assert owners == {"_atomic_write_text"}, (
            f"the line-ending restore has more than one owner: {sorted(sites.items())}. "
            f"The seam applies the ending for every caller, so a second site "
            f"runs the substitution twice on the same document. Remove the "
            f"call-site restore, or state in this commit why this caller must "
            f"own the ending itself"
        )

    def test_each_twin_carries_exactly_one_restore(self):
        """ONE owner for each twin, not one owner across the two.

        A single seam restore with the OTHER twin left unrepaired satisfies the
        arm above, because the surviving owner is still named
        `_atomic_write_text`. This is the half that catches a one-twin repair.
        """
        sites, _ = self._sites()
        twins = (
            "hooks/shared/claude_md_manager.py",
            "skills/pact-memory/scripts/working_memory.py",
        )

        for twin in twins:
            assert sites.get(twin) == ["_atomic_write_text"], (
                f"{twin} carries {sites.get(twin)!r} restore call sites inside "
                f"{self._RESTORE!r}'s owner. Each twin must restore exactly "
                f"once, inside its own seam: one repaired twin and one "
                f"unrepaired twin makes the hook and the skill write the same "
                f"file with different line endings"
            )


class TestRestoreNormalisesBeforeItApplies:
    """`_restore_line_ending` converts CRLF to LF BEFORE it applies the ending.

    WHY THE TWO GATES ABOVE CANNOT REACH THIS, AND IT IS THE WHOLE REASON THIS
    CLASS IS HERE. A removal of the normalise step applied to the TWO twins
    alike is invisible to each of them, for a different reason:
      - TestLineEndingHelperTwinCopyDrift compares the two twins AGAINST EACH
        OTHER, so a symmetric removal keeps them identical and that gate stays
        silent BY CONSTRUCTION.
      - TestRestoreHasOneOwnerPerTwin counts CALL SITES, and a step removed
        INSIDE the helper changes no count, so that gate stays silent too.
    A symmetric removal sits in the blind spot of the two. The merge-guard
    docstring above names this hazard in prose. Prose that names a hazard is
    not a guard against it.

    WHAT A REMOVAL COSTS, MEASURED RATHER THAN FEARED. Content that already
    carries CRLF meets a plain `.replace("\\n", line_ending)` and each ending
    becomes "\\r\\r\\n". That is corruption of the user's own text, and
    CLAUDE.md is frequently gitignored, so no commit brings the original back.

    THE STEP IS REACHABLE IN THE SHIPPED TREE, AND THE TRIGGER IS A
    CONJUNCTION. The target must be CRLF-dominant, AND the content must carry a
    carriage return. The content does NOT get one from the document: each of
    the seam call sites reads the target with `read_text(encoding="utf-8")`,
    which translates. It gets one by COMPOSITION, because a caller interpolates
    an external payload into the text it hands the seam. So this arm guards a
    live path rather than a hypothetical future one.
    """

    # THE TWO TWINS, as import targets. Parametrized rather than looped so a
    # failure names WHICH copy broke.
    _TWINS = ("shared.claude_md_manager", "scripts.working_memory")

    @staticmethod
    def _restore(module_name):
        import importlib

        return importlib.import_module(module_name)._restore_line_ending

    @pytest.mark.parametrize("twin", _TWINS)
    def test_crlf_content_does_not_gain_a_doubled_carriage_return(self, twin):
        """THIS IS THE DISCRIMINATING ARM. Remove the normalise step and it
        reddens in EACH twin.

        Input carries CRLF and the target ending is CRLF, so the correct output
        is the input unchanged. Without the normalise step each "\\n" is
        rewritten while its leading "\\r" survives, giving "\\r\\r\\n".
        """
        restore = self._restore(twin)

        result = restore("a\r\nb\r\n", "\r\n")

        assert "\r\r\n" not in result, (
            f"{twin}: the restore produced a DOUBLED carriage return "
            f"{result!r}. It applied the ending without normalising first, so "
            f"content that already carries CRLF is corrupted. Restore the "
            f'.replace("\\r\\n", "\\n") step before the ending is applied'
        )
        assert result == "a\r\nb\r\n", (
            f"{twin}: expected the CRLF input back unchanged, got {result!r}"
        )

    @pytest.mark.parametrize("twin", _TWINS)
    def test_lf_content_still_converts_to_the_target_ending(self, twin):
        """NON-VACUITY FOR THE ARM ABOVE. A `_restore_line_ending` that
        returned its input unchanged would satisfy the doubling assertion
        PERFECTLY and convert nothing, so that arm is evidence only once the
        function is known to convert at all."""
        restore = self._restore(twin)

        assert restore("a\nb\n", "\r\n") == "a\r\nb\r\n", (
            f"{twin}: the restore did not convert LF to the CRLF target, so "
            f"the doubling arm above is passing on a function that does "
            f"nothing"
        )

    @pytest.mark.parametrize("twin", _TWINS)
    def test_an_lf_target_returns_the_content_untouched(self, twin):
        """PINS A DIFFERENT PROPERTY, AND IT IS NAMED SO NOBODY COUNTS IT AS
        COVERAGE OF THE NORMALISE STEP. The LF branch returns early, ABOVE the
        normalise step, so this arm stays green when that step is removed. It
        pins that an LF file is byte-identical to what the seam wrote before
        the restore moved here."""
        restore = self._restore(twin)

        assert restore("a\r\nb\r\n", "\n") == "a\r\nb\r\n"
        assert restore("a\nb\n", "\n") == "a\nb\n"


class TestContainmentErrorTwinCopyDrift:
    """Drift detection for the ContainmentError twin.

    `working_memory` vendors this from `hooks/shared/claude_md_manager` for the
    same reason as its siblings — skills/ cannot import from hooks/shared/ —
    and it is the exception the containment check raises, so the two copies
    disagreeing means the hook and skill paths signal a containment failure
    differently.

    IT IS GATED ON AST EQUALITY, NOT BYTE EQUALITY, and that is not a
    weakening. The two copies are already not byte-identical: each docstring
    points at the other, which is exactly the divergence the sibling gates
    permit by stripping docstrings before comparing. A byte gate here would go
    red on day one, and a gate that is red on arrival gets deleted rather than
    investigated.
    """

    @staticmethod
    def _executable_shape(obj) -> str:
        """AST dump of the definition with any leading docstring removed."""
        node = ast.parse(textwrap.dedent(inspect.getsource(obj))).body[0]
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)):
            node.body = node.body[1:]
        return ast.dump(node)

    def test_containment_error_shapes_are_identical(self):
        from shared.claude_md_manager import ContainmentError as canonical
        from scripts.working_memory import ContainmentError as twin

        assert self._executable_shape(canonical) == self._executable_shape(twin), (
            "ContainmentError twin drift between "
            "hooks/shared/claude_md_manager.py and "
            "skills/pact-memory/scripts/working_memory.py — the containment "
            "failure signal must stay identical across the copies; update "
            "both in the SAME commit."
        )

    def test_the_gate_can_tell_the_shapes_apart(self):
        """NON-VACUITY. An AST comparison that returns equal for everything
        would pass the assertion above forever."""
        from scripts.working_memory import ContainmentError as twin
        from scripts.working_memory import _project_root_of as unrelated

        assert self._executable_shape(twin) != self._executable_shape(unrelated), (
            "the shape extractor cannot distinguish two different definitions, "
            "so the drift assertion above proves nothing"
        )


class TestProjectRootLayoutKnowledgeDrift:
    """`_project_root_of` duplicates LAYOUT KNOWLEDGE, not a function body.

    Its siblings above are twin-copied implementations, compared against their
    originals. This one has no original to compare with: it is the INVERSE of
    `claude_md_manager.resolve_project_claude_md_path` (path -> root rather
    than root -> path), so no copy of it exists to diverge from. What it
    duplicates is the two-layout rule — that CLAUDE.md lives at
    `<project>/.claude/CLAUDE.md` or `<project>/CLAUDE.md` — whose SSOT is
    `_DOT_CLAUDE_RELATIVE` / `_LEGACY_RELATIVE`.

    SO IT IS PINNED AGAINST THE CONSTANTS RATHER THAN AGAINST A TWIN, and the
    difference matters: measured, renaming the SSOT constant breaks 21 tests
    elsewhere in the tree and ZERO in the memory layer. The knowledge is fully
    decoupled, so a third supported location — or a rename — diverges here in
    silence. `archive_pin.project_dir_for` carries the same rule and the same
    exposure.
    """

    def test_inverts_the_canonical_resolver_for_both_layouts(self):
        from shared.claude_md_manager import (
            _DOT_CLAUDE_RELATIVE,
            _LEGACY_RELATIVE,
            resolve_project_claude_md_path,
        )
        from scripts.working_memory import _project_root_of

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # Legacy layout: seed the file the SSOT constant names.
            legacy_root = root / "legacy"
            (legacy_root).mkdir()
            (legacy_root / _LEGACY_RELATIVE).write_text("x", encoding="utf-8")
            resolved, source = resolve_project_claude_md_path(legacy_root)
            assert source == "legacy", f"fixture drift: got source={source!r}"
            assert _project_root_of(resolved) == legacy_root, (
                "_project_root_of does not invert the canonical resolver for "
                f"the legacy layout named by _LEGACY_RELATIVE ({_LEGACY_RELATIVE!r})"
            )

            # Dot layout.
            dot_root = root / "dot"
            (dot_root / _DOT_CLAUDE_RELATIVE).parent.mkdir(parents=True)
            (dot_root / _DOT_CLAUDE_RELATIVE).write_text("x", encoding="utf-8")
            resolved, source = resolve_project_claude_md_path(dot_root)
            assert source == "dot_claude", f"fixture drift: got source={source!r}"
            assert _project_root_of(resolved) == dot_root, (
                "_project_root_of does not invert the canonical resolver for "
                f"the dot layout named by _DOT_CLAUDE_RELATIVE "
                f"({_DOT_CLAUDE_RELATIVE!r}) — a third supported location or a "
                f"rename of that constant diverges here silently"
            )


def at_budget_body(prefix=""):
    """Build the LARGEST pin body that still sits within the budget.

    DERIVED BY SEARCH FROM THE LIVE CONSTANT, so it follows the budget to
    whatever value it takes next, and it assumes nothing about the shape of
    `estimate_tokens`. The two assertions below are real boundary checks and
    CAN fire: the first if the search overshot, the second if one more word
    does not cross. Together they pin this fixture to the exact edge rather
    than merely somewhere below it.
    """
    from staleness import PINNED_CONTEXT_TOKEN_BUDGET, estimate_tokens

    # A body of BUDGET words always estimates above BUDGET, so it bounds the
    # search from above without naming a number.
    low, high = 1, PINNED_CONTEXT_TOKEN_BUDGET
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(prefix + "word " * mid) <= PINNED_CONTEXT_TOKEN_BUDGET:
            low = mid
        else:
            high = mid - 1

    body = prefix + "word " * low
    assert estimate_tokens(body) <= PINNED_CONTEXT_TOKEN_BUDGET, (
        "at-budget fixture is already over the budget"
    )
    assert estimate_tokens(body + "word ") > PINNED_CONTEXT_TOKEN_BUDGET, (
        "at-budget fixture is not at the edge -- one more word must cross it"
    )
    return body


class TestBudgetWarningInteractions:
    """The cells where the warning path MEETS the rest of this module.

    Every arm here exists because a mutation of a property the source calls
    load-bearing survived the whole suite. A comment is a claim; these are the
    witnesses. Each docstring names the mutation it kills, so a later reader can
    re-run the counter-test instead of re-deriving it.
    """

    def _create_project_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def _warning_line(self, tokens=9, budget=5):
        """A warning line built from the SOURCE constant, never from a copy.

        Uses the module-level `_BUDGET_WARNING_PREFIX` import. The body shape
        matters as well as the prefix: the strip predicate requires a
        well-formed `~N tokens (budget: N)` payload, so a line assembled by hand
        would exercise a different branch from the one the hook writes.
        """
        return (
            f"{_BUDGET_WARNING_PREFIX} ~{tokens} tokens (budget: {budget}). "
            "Consider archiving stale pins. -->\n"
        )

    def _count_warnings(self, text):
        return text.count(_BUDGET_WARNING_PREFIX)

    def _doc(self, body):
        """A document whose pinned region is bounded by a following section."""
        return f"# Project Memory\n\n## Pinned Context\n\n{body}\n## Working Memory\nwm\n"

    # ------------------------------------------------------------------
    # The warning path meeting the stale-marker path.
    # ------------------------------------------------------------------

    def test_stale_marker_survives_a_leading_warning(self, tmp_path):
        """A document with BOTH a warning and a stale entry must still be marked.

        KILLS THE ORDER MUTATION. `apply_staleness_markings` strips the warning
        BEFORE it takes entry offsets, because `entry_starts` holds offsets into
        that string. Move the strip after the scan and the stale marker is
        silently dropped -- one becomes zero, which is this module's primary
        function failing quietly.

        Measured before this arm existed: that inversion was killed by NO test
        in the suite. Both arms were run over the full suite in one environment
        and the failed sets came back byte-identical, so the test set did not
        distinguish the two programs.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc(
                self._warning_line()
                + "### Old Decision\nPR #123, merged 2020-01-01\n"
                + f"### Big Feature\n{over_budget_body()}\n"
            ),
        )

        check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        assert content.count("<!-- STALE: Last relevant 2020-01-01 -->") == 1, (
            "the stale marker was dropped -- entry offsets and the stripped "
            "body have gone out of step"
        )
        assert "PR #123, merged 2020-01-01" in content
        assert self._count_warnings(content) == 1

    # ------------------------------------------------------------------
    # The strip removes a RUN, not a line.
    # ------------------------------------------------------------------

    def test_stacked_warnings_collapse_to_one(self, tmp_path):
        """Two warnings at the head must BOTH be taken back.

        KILLS THE SINGLE-SHOT STRIP. `_strip_budget_warnings` loops on purpose,
        so that removing N lines is the exact inverse of writing one and no
        partial residue survives. Replace the loop with a single match and this
        document keeps a stale line forever; nothing else in the suite notices.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc(
                self._warning_line(tokens=9)
                + self._warning_line(tokens=11)
                + f"### Big Feature\n{over_budget_body()}\n"
            ),
        )

        check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        assert self._count_warnings(content) == 1
        # Neither stale figure survives: the run went, not merely its head.
        assert "~9 tokens" not in content
        assert "~11 tokens" not in content

    # ------------------------------------------------------------------
    # The accepted residual. The count is CONDITIONAL, not a constant.
    # ------------------------------------------------------------------
    #
    #   count = N + (1 if estimate_tokens(user_text + stranded) > BUDGET else 0)
    #
    # N is the number of warnings a user has MOVED below the head, where the
    # anchored strip cannot reach them. The arms below pin both halves of that
    # conditional and the fact that a pass never raises the count. A single arm
    # would state a ceiling instead of a law: two correct measurements of this
    # behaviour, taken at N=1 and N=3, once read as a contradiction for exactly
    # that reason.

    def test_no_warning_when_the_body_sits_exactly_at_budget(self, tmp_path):
        """At the budget with nothing stranded, the count is ZERO.

        THE CELL A CONSTANT FORM GETS WRONG. The comparison is strict
        (`tokens > BUDGET`), so a body landing exactly ON the budget is not a
        breach. A `1 + N` reading of the residual predicts one warning here,
        and there must be none.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path, self._doc(at_budget_body(prefix="### Big\n"))
        )

        result = check_pinned_staleness(claude_md_path=claude_md)

        assert self._count_warnings(claude_md.read_text(encoding="utf-8")) == 0
        assert result is None

    def test_a_stranded_copy_cannot_cause_the_breach_it_reports(self, tmp_path):
        """The residue of the module is excluded from the COUNT, and it is KEPT.

        THE SUBJECT HAS TWO HALVES AND THE SECOND IS THE ONE A LATER
        SIMPLIFICATION WILL TRY TO REMOVE.
          1. A warning line the anchored strip cannot reach contributes NO token
             to the measurement, so the pins of the user decide the breach and
             the residue of this module does not. With those pins sitting
             exactly at the budget, the pass reports nothing.
          2. THE MODULE DOES NOT DELETE THAT LINE TO ACHIEVE IT. The exclusion
             runs on a throwaway copy at the measurement site. An in-place
             exclusion scores the same on the count and takes a line out of a
             file that is frequently gitignored.

        THIS ARM REPLACES ONE THAT PINNED THE DEFECT. The arm it replaces held
        the opposite subject, that a stranded copy CAN cause the breach it
        reports, and it asserted a count of 2 with a budget status string. That
        was the accepted residual of the day. The measurement no longer counts
        the output of this module, so the residual is closed rather than
        accepted, and the arm wanted a retarget rather than a repair.

        WHY THE FILE BYTES ARE ASSERTED. MEASURED on this fixture: the correct
        fix gives 1 warning, `None`, and UNCHANGED bytes. An in-place exclusion
        gives 0 warnings, `None`, and the stranded line DELETED. The return
        value is `None` in the two arms, so an arm built on it alone is blind to
        the corrupting variant. The count and the bytes are what separate them.
        """
        from staleness import PINNED_CONTEXT_TOKEN_BUDGET, check_pinned_staleness
        from staleness import estimate_tokens

        user_text = at_budget_body(prefix="### Big\n")
        words_only = user_text.split("\n", 1)[1]
        stranded = self._warning_line(tokens=9999)
        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc("### Big\n" + stranded + words_only),
        )
        before = claude_md.read_bytes()

        result = check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        # The pins of the user were never over budget, and the residue no longer
        # pushes them over.
        assert estimate_tokens(user_text) <= PINNED_CONTEXT_TOKEN_BUDGET
        assert result is None, (
            f"the hook reported {result!r}. It is counting its own output "
            f"again, so a line this module wrote is causing the breach it "
            f"reports"
        )
        # The line the strip cannot reach is the only one left, and it is where
        # the user left it.
        assert self._count_warnings(content) == 1
        assert stranded in content, "the strip reached below the head"
        assert claude_md.read_bytes() == before, (
            "the pass rewrote the file. The exclusion is running IN PLACE, so "
            "it deletes a line the user positioned from a file that is "
            "frequently gitignored and has no commit to recover from"
        )

    @pytest.mark.parametrize("stranded", [0, 1, 3])
    def test_stranded_warnings_never_compound(self, tmp_path, stranded):
        """Over budget, the module adds exactly ONE line however many are stranded.

        The other half of the conditional: once the measured body exceeds the
        budget the count settles at `stranded + 1`, and NO PASS EVER RAISES IT.
        That second property is the one that matters -- the head cannot
        accumulate, because the run at offset 0 is removed before one line goes
        back. Neutering the strip makes every cell here fail.
        """
        from staleness import check_pinned_staleness

        stranded_lines = "".join(
            self._warning_line(tokens=100 + i) for i in range(stranded)
        )
        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc(f"### Big Feature\n{stranded_lines}{over_budget_body()}\n"),
        )

        check_pinned_staleness(claude_md_path=claude_md)
        settled = claude_md.read_text(encoding="utf-8")
        assert self._count_warnings(settled) == stranded + 1

        # Three further passes must not add a fourth, a fifth, or a sixth.
        for _ in range(3):
            check_pinned_staleness(claude_md_path=claude_md)
        after = claude_md.read_text(encoding="utf-8")
        assert self._count_warnings(after) == stranded + 1, (
            "a pass raised the count -- the head is accumulating"
        )
        assert after == settled, "a later pass rewrote a settled document"

    # ------------------------------------------------------------------
    # The entry-less asymmetry, over-budget half.
    # ------------------------------------------------------------------

    def test_entryless_section_refreshes_a_warning_it_already_has(self, tmp_path):
        """An entry-less section that is STILL over budget keeps a CURRENT warning.

        The half of the asymmetry the suite did not cover. `check_pinned_staleness`
        proceeds on an entry-less section only when a warning is already there,
        and the existing arms cover only the case where the body has since
        dropped under budget. Here it has not, so the line must be REWRITTEN
        with a true figure rather than left carrying its old one.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc(self._warning_line(tokens=9) + f"{over_budget_body()}\n"),
        )

        check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        assert self._count_warnings(content) == 1
        assert "~9 tokens" not in content, "the outdated figure was left in place"
        assert warning_number(content) > 0

    # ------------------------------------------------------------------
    # The entry-less asymmetry, widened-probe half.
    # ------------------------------------------------------------------
    #
    # The probe reads ANY line start; the strip reads offset 0. The three arms
    # below pin the whole of that gap: what the wider probe buys, what it
    # costs, and the second condition that keeps the cost from firing.

    def test_entryless_section_with_a_stranded_warning_is_refreshed(self, tmp_path):
        """A warning MOVED below the head still admits the section to the pass.

        THE DEFECT THIS REMOVES. The probe used to be anchored at offset 0, so
        a user who moved the warning down took the whole section out of the
        pass: the figure froze at whatever it said that day, and no later pass
        could correct it. A wider probe admits the section again.

        THE STRIP DOES NOT FOLLOW THE PROBE. It still reaches offset 0 only, so
        the moved line survives and this pass adds ONE current warning ABOVE
        it. Two lines, not one. That is the ratified residual, not a repair.

        KILLS THE ANCHOR REVERT. Put `_LEADING_BUDGET_WARNING_RE.match` back
        into `_has_budget_warning` and this document is left untouched with its
        frozen figure, which is the defect itself.
        """
        from staleness import check_pinned_staleness

        stranded = self._warning_line(tokens=9999)
        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc(
                "Some prose the user keeps here.\n"
                + stranded
                + f"{over_budget_body()}\n"
            ),
        )

        result = check_pinned_staleness(claude_md_path=claude_md)

        content = claude_md.read_text(encoding="utf-8")
        # One line the hook wrote at the head, plus the one it cannot reach.
        assert self._count_warnings(content) == 2
        assert stranded in content, "the strip reached a line below the head"
        head_figure = warning_number(content)
        assert head_figure is not None, "the pass wrote no warning at the head"
        assert head_figure != 9999, (
            "the head still carries the stranded figure -- the number froze, "
            "which is the defect this widening removes"
        )
        assert result is not None and "budget" in result.lower()

        # The pass settles. Later passes must change no byte and add no line.
        settled = content
        for _ in range(3):
            check_pinned_staleness(claude_md_path=claude_md)
        assert claude_md.read_text(encoding="utf-8") == settled, (
            "a later pass rewrote a settled document"
        )

    def test_a_pasted_warning_over_budget_costs_exactly_one_extra_line(self, tmp_path):
        """THE RULING ITSELF, pinned so the accepted cost can be falsified.

        A maintainer who pastes the emitted format into a note of their own
        writes a document this module CANNOT tell apart from the stranded case
        above. The shape is what identifies a line as this module's own, and
        both documents carry that shape at a line start below the head. So this
        section enters the pass too, and gains one current warning above the
        paste. This arm states the price rather than asserting that the price
        is acceptable.

        THE PRICE IS BOUNDED AT ONE EXTRA LINE, which is the half that matters.
        Six passes hold the count at two and change no byte after the first, so
        a maintainer's note cannot make the document grow without limit.

        THE USER'S OWN LINE MUST SURVIVE, and that is the cardinal assertion
        here. This module deletes from a file that is frequently gitignored, so
        an over-broad strip removes text no commit can restore.
        """
        from staleness import check_pinned_staleness

        pasted = self._warning_line(tokens=99, budget=1)
        claude_md = self._create_project_claude_md(
            tmp_path,
            self._doc(
                "The hook writes this line:\n"
                + pasted
                + f"{over_budget_body()}\n"
            ),
        )

        result = check_pinned_staleness(claude_md_path=claude_md)

        settled = claude_md.read_text(encoding="utf-8")
        assert self._count_warnings(settled) == 2
        assert pasted in settled, "the strip deleted a line a user wrote"
        assert result is not None and "budget" in result.lower()

        # Five further passes. The accepted cost is ONE extra line for ever,
        # not one extra line per pass.
        for _ in range(5):
            check_pinned_staleness(claude_md_path=claude_md)
        after = claude_md.read_text(encoding="utf-8")
        assert self._count_warnings(after) == 2, (
            "a later pass raised the count -- the accepted cost is not bounded"
        )
        assert after == settled, "a later pass rewrote a settled document"

    def test_a_pasted_warning_under_budget_changes_nothing(self, tmp_path):
        """BOTH CONDITIONS ARE REQUIRED, so the paste alone is inert.

        The wider probe only ADMITS a section to the pass. The write decision
        is still `pinned_tokens > PINNED_CONTEXT_TOKEN_BUDGET`, and the probe
        is not an input to it. The same paste in a body UNDER the budget
        therefore leaves the document byte-identical and returns None.

        A POSITIVE LEG SITS IN THIS FIXTURE, and it is what makes the untouched
        assertion mean anything. "Nothing happened" also passes for a hook that
        never ran, a region that never parsed, and a renamed constant. The
        second half of this test is the SAME document over the budget, which
        must gain exactly one line.
        """
        from staleness import check_pinned_staleness

        pasted = self._warning_line(tokens=99, budget=1)
        quiet = self._create_project_claude_md(
            tmp_path,
            self._doc("The hook writes this line:\n" + pasted + "a short pin\n"),
        )
        before = quiet.read_text(encoding="utf-8")

        result = check_pinned_staleness(claude_md_path=quiet)

        assert result is None
        assert quiet.read_text(encoding="utf-8") == before, (
            "an under-budget body was modified -- the paste alone must be inert"
        )
        assert self._count_warnings(before) == 1

        # POSITIVE CONTROL: the same document over the budget, in the same
        # test. Without it the assertions above hold for a dead instrument.
        sub = tmp_path / "sub"
        sub.mkdir()
        loud = self._create_project_claude_md(
            sub,
            self._doc(
                "The hook writes this line:\n"
                + pasted
                + f"{over_budget_body()}\n"
            ),
        )
        assert check_pinned_staleness(claude_md_path=loud) is not None
        assert self._count_warnings(loud.read_text(encoding="utf-8")) == 2, (
            "the positive control did not fire, so the untouched assertion "
            "above proves nothing about the budget condition"
        )

    # ------------------------------------------------------------------
    # Absence must be caused by the budget, not by a dead instrument.
    # ------------------------------------------------------------------

    def test_under_budget_absence_is_caused_by_the_budget(self, tmp_path):
        """A small document gets no warning, and this arm proves WHY it got none.

        A POSITIVE LEG IN THE SAME FIXTURE. `test_under_budget_no_warning`
        asserts only that the literal is absent, so it passes for any reason at
        all -- a renamed constant, a broken region parse, or the hook never
        running. Measured: with the write path neutered so that no warning is
        ever emitted, that test still PASSED. Absence is not evidence until
        something in the same test shows the instrument fires, and a
        renamed-constant tripwire does not supply that.
        """
        from staleness import check_pinned_staleness

        claude_md = self._create_project_claude_md(
            tmp_path, self._doc("### Small\n- a few words\n")
        )
        check_pinned_staleness(claude_md_path=claude_md)
        assert self._count_warnings(claude_md.read_text(encoding="utf-8")) == 0

        # POSITIVE CONTROL: the same shape, over budget, in the same test.
        sub = tmp_path / "sub"
        sub.mkdir()
        big = self._create_project_claude_md(
            sub, self._doc(f"### Small\n{over_budget_body()}\n")
        )
        check_pinned_staleness(claude_md_path=big)
        assert self._count_warnings(big.read_text(encoding="utf-8")) == 1, (
            "the positive control did not fire, so the negative arm above "
            "proves nothing"
        )


# ===========================================================================
# The two budget-warning predicates are one shape under two anchors
# ===========================================================================


def _line_starts(text):
    """Every offset a `(?m)^` pattern can match at: 0, and after each newline.

    A text that ends with a newline has a line start at its very end, where
    there is nothing left to match. That position is included on purpose, so
    this list is the multiline anchor's own position set rather than an
    approximation of it. Measured against `re.finditer(r"(?m)^", ...)`.
    """
    return [0] + [match.end() for match in re.finditer(r"\n", text)]


_ONE_WARNING_LINE = (
    f"{_BUDGET_WARNING_PREFIX} ~42 tokens (budget: 5). "
    "Consider archiving stale pins. -->\n"
)

# THE CORPUS IS KEYED BY NAME so a failure reports WHICH member broke the
# property instead of an index. Every member is built from the imported
# prefix, never from a copied literal.
#
# THE THREE CLASSES BELOW ARE COUNTED BY THE SECOND ARM, and each class earns
# its place. The SEPARATING members are the only reason the equality property
# says anything at all. The BOTH-TRUE members keep it from being a proof that
# nothing ever matches. The BOTH-FALSE members are the shapes a user's own
# prose can take, and they are what shows the wider anchor did not widen the
# ALPHABET: a bare prefix, a payload with no digits, a line with no terminator,
# and a warning that starts mid-line all stay outside both predicates.
#
# LINE ENDINGS ARE OUT OF SCOPE HERE and no member carries a carriage return.
# The two predicates share one shape, so they cannot disagree about a line
# ending, and the arc that produced them changed nothing about that behaviour.
_ANCHOR_CORPUS = {
    "empty": "",
    "prose only": "an ordinary pinned note\n",
    "leading warning": _ONE_WARNING_LINE + "the rest of the body\n",
    "two leading warnings": _ONE_WARNING_LINE * 2 + "the rest of the body\n",
    "stranded below the head": (
        "an ordinary pinned note\n" + _ONE_WARNING_LINE + "more prose\n"
    ),
    "warning on the last line, no newline": (
        "an ordinary pinned note\n" + _ONE_WARNING_LINE.rstrip("\n")
    ),
    "warning starts mid line": "quoted: " + _ONE_WARNING_LINE,
    "bare prefix, no payload": (
        f"{_BUDGET_WARNING_PREFIX} is what the hook writes -->\n"
    ),
    "payload without digits": f"{_BUDGET_WARNING_PREFIX} ~ tokens (budget: ). -->\n",
    "payload without a terminator": (
        f"{_BUDGET_WARNING_PREFIX} ~42 tokens (budget: 5). Consider archiving\n"
    ),
    "terminator on the following line": (
        f"{_BUDGET_WARNING_PREFIX} ~42 tokens (budget: 5).\n-->\n"
    ),
}


class TestBudgetWarningAnchorComposition:
    """One shape, two anchors, and the wider one must not widen the alphabet.

    `_LEADING_BUDGET_WARNING_RE` DELETES and `_ANY_BUDGET_WARNING_RE` only
    RECOGNISES. They are composed from the same `_BUDGET_WARNING_SHAPE` so that
    the difference between them is a position and nothing else. These arms are
    what hold that claim up.

    WHAT THIS DELIBERATELY DOES NOT DO. It compares no pattern STRINGS. An
    assertion that one pattern ends with the shared shape certifies nothing
    about behaviour: two patterns can share a fragment and still match
    different sets, and two IDENTICAL patterns satisfy such an assertion
    perfectly. Both arms below CALL the compiled objects instead, because only
    the shipped objects can testify about their own composition.

    THE TWO ARMS HAVE DIFFERENT SUBJECTS, WHICH IS WHY THERE ARE TWO. The first
    is about the PATTERNS; the second is about what the corpus can still tell
    apart. THREE STATES WERE RUN, and two of them separate the arms.
      - TRIM the separating members out of the corpus: the equality arm still
        PASSES, the second goes red.
      - BREAK the shared shape to a literal that never matches: the equality
        arm PASSES AGAIN, because both sides then say False to everything, and
        the second still goes red.
      - RE-ANCHOR the wide pattern, so the two are identical patterns: BOTH go
        red.
    The first arm alone therefore certifies nothing. That is the whole reason
    the second one exists, and it is why neither may be deleted as a duplicate.

    WHAT THIS PAIR DOES NOT COVER, AND IT IS THE MODULE'S CARDINAL HAZARD.
    MEASURED: widen the DELETING pattern's anchor and the two arms stay GREEN.
    The equality arm slices the text, and each anchor matches at a slice start,
    so the two sides agree on every member. The arms are not weak here. They
    measure a property that the widening does not disturb. That widening has
    its own gate below, in `TestDeletingAnchorStaysAtTheHead`.
    """

    def test_the_wide_anchor_matches_at_exactly_the_narrow_one_s_positions(self):
        """The wide predicate equals the narrow one tried at every line start.

        This is the composition property stated as behaviour. If the wide
        pattern ever gains a token the narrow one lacks, or loses one it has,
        the two sides part company on some member of the corpus.
        """
        from staleness import _ANY_BUDGET_WARNING_RE, _LEADING_BUDGET_WARNING_RE

        for name, text in _ANCHOR_CORPUS.items():
            wide = _ANY_BUDGET_WARNING_RE.search(text) is not None
            # SLICE THE TEXT. DO NOT PASS AN OFFSET AS `pos`. The two look
            # interchangeable and are not. `\A` matches only at the TRUE start
            # of the string, and `pos` DOES NOT MOVE IT, so
            # `.match(text, k)` with k > 0 returns None for every input --
            # whatever the shape is, and whether the shape is right or broken.
            # MEASURED, with a control: against a deliberately broken pattern
            # the `pos` form gives None for both, which is INDISTINGUISHABLE,
            # while the slice form matches for the real one and not for the
            # broken one, which SEPARATES. So the natural-looking `pos`
            # spelling turns this whole arm vacuous, and it would still pass.
            # Slicing re-anchors to the slice start, which is what actually
            # exercises the shipped object.
            #
            # THIS CALL SHAPE IS LEGITIMATE HERE AND FORBIDDEN IN THE MODULE.
            # `_LEADING_BUDGET_WARNING_RE` is the pattern that DELETES, and
            # giving it a caller-chosen offset is the construction the module
            # refuses: it moves the anchor out of the compiled object and into
            # a convention, where a later caller can widen what gets deleted.
            # Nothing here deletes a byte, so that hazard cannot arise, and
            # only the shipped object can testify about its own composition.
            # DO NOT COPY THIS LINE INTO A CALLER.
            narrow_anywhere = any(
                _LEADING_BUDGET_WARNING_RE.match(text[k:])
                for k in _line_starts(text)
            )
            assert wide == narrow_anywhere, (
                f"the two anchors disagree on {name!r}: the wide pattern says "
                f"{wide}, the narrow one tried at every line start says "
                f"{narrow_anywhere}. They no longer share one shape."
            )

    def test_the_corpus_separates_the_two_anchors(self):
        """NON-VACUITY, and it guards the CORPUS rather than the predicates.

        TWO PREDICATES AGREE FOR FREE WHEN BOTH SAY FALSE TO EVERYTHING. That
        is the trap under the equality arm, and it has two entrances. A corpus
        of only negative members passes it. So does a corpus of positives, if
        the shared shape stops matching anything at all. The arm above cannot
        see either one, because in both states its two sides agree perfectly.

        SO THE CORPUS MUST CARRY BOTH KINDS AT ONCE: members that BOTH sides
        match, and among them a member that only the LINE-START side can reach.
        The first refuses a shape that matches nothing; the second refuses two
        patterns that are secretly one.

        MEASURED, NOT ASSERTED. Replace `_BUDGET_WARNING_SHAPE` with a literal
        that can never match, and the equality arm above PASSES while this one
        goes red. Re-anchor the wide pattern to the start of the string, and
        this one goes red as well. Both states were run.

        THE COUNTS ARE PINNED, NOT MERELY NON-ZERO. "At least one" survives a
        trim down to one lucky member. An exact figure per class refuses any
        trim, and names the class that went missing. Add a member and this arm
        asks you to say which class it joined.
        """
        from staleness import _ANY_BUDGET_WARNING_RE, _LEADING_BUDGET_WARNING_RE

        separating, both_true, both_false = [], [], []
        for name, text in _ANCHOR_CORPUS.items():
            wide = _ANY_BUDGET_WARNING_RE.search(text) is not None
            at_head = _LEADING_BUDGET_WARNING_RE.match(text) is not None
            if wide and not at_head:
                separating.append(name)
            elif wide:
                both_true.append(name)
            else:
                both_false.append(name)

        # THE TWO ENTRANCES TO THE TRAP GET SEPARATE MESSAGES, because they
        # have different repairs and a reader who reaches this line is trying
        # to work out which one they broke.
        assert separating or both_true, (
            "the wide predicate matched NOTHING anywhere in the corpus. Either "
            "the shared shape no longer matches the text this module writes, "
            "or every positive member has left the corpus. The equality arm "
            "above PASSES in that state, because two predicates that both say "
            "False agree for free"
        )
        assert separating, (
            "no corpus member is SEPARATING: for each member the wide "
            "predicate agrees with the narrow one at offset 0. Either the "
            "corpus lost its below-head member, or the wide predicate no "
            "longer sees that member because its anchor is narrower. The "
            "equality arm above names which. The property here would pass "
            "unchanged if the two patterns were the same"
        )
        assert len(separating) == 2, f"separating members: {sorted(separating)}"
        assert len(both_true) == 2, f"both-true members: {sorted(both_true)}"
        assert len(both_false) == 7, f"both-false members: {sorted(both_false)}"


class TestDeletingAnchorStaysAtTheHead:
    """The pattern that DELETES must not reach past offset 0, by any call.

    THE HAZARD HERE IS THE CARDINAL ONE OF THIS MODULE. Widen this anchor and
    a strip reaches text a user wrote inside a pin body, in a file that is
    frequently gitignored, with no commit to recover from. The module refuses
    that widening in its own prose. Until this arm, nothing enforced it.

    WHY NO DOCUMENT-LEVEL ARM CATCHES IT, which is the reason this gate reads
    the OBJECT. `_strip_budget_warnings` reaches the pattern by `.match()` at
    offset 0 alone, and each anchor matches at offset 0. MEASURED: with the
    anchor widened to a line-anchored form, the strip returns BYTE-IDENTICAL
    output and the whole file passes. The widening changes no behaviour today.
    It removes a GUARANTEE, and a guarantee is invisible to an arm that drives
    documents. The composition arms above miss it for a different reason, which
    their own docstring records.

    WHY `.search` IS THE PROBE. A start-of-string anchor makes `.search` and
    `.match` agree on every input, so `.search` is harmless as a call site.
    THAT AGREEMENT IS THE PROPERTY. If `.search` reaches further than `.match`
    can, the pattern no longer pins itself to offset 0.

    THIS GATE HOLDS NO LITERAL. It reads neither the pattern string nor the
    module source, so a legitimate re-spelling passes. MEASURED: a caret with
    no MULTILINE flag is equivalent to the shipped anchor, carries no hazard,
    and this arm passes it. A gate on the pattern string REFUSES that spelling,
    which is why this one does not read the string.

    WHAT A LATER EDIT MUST DO TO SATISFY IT. Keep the compiled object unable to
    match anywhere except offset 0. The sanctioned repair for the residual this
    module accepts does NOT widen the delete: it excludes lines of this shape
    from the COUNT at the measurement site, on a throwaway copy, and leaves the
    delete on the contiguous run at the head.
    """

    def test_the_deleting_pattern_cannot_reach_below_the_head(self):
        """A warning below the head must be invisible to the deleting pattern.

        THE NEGATIVE LEG IS THE GATE. `.search` scans every position, so it
        reports a match wherever the pattern is able to match at all. On a body
        whose only warning sits below the head, this pattern must find nothing.

        THE POSITIVE LEG SITS IN THE SAME TEST, because "found nothing" is also
        what a shape that matches nothing produces. Without it this gate would
        report the cardinal hazard as covered while it checked nothing.
        """
        from staleness import _LEADING_BUDGET_WARNING_RE

        below_head = "a note the user wrote\n" + _ONE_WARNING_LINE + "more prose\n"
        assert _LEADING_BUDGET_WARNING_RE.search(below_head) is None, (
            "the deleting pattern reached a warning BELOW the head, so a strip "
            "built on it now removes text a user wrote inside a pin body, from "
            "a file that is frequently gitignored. Do not widen this anchor. "
            "Exclude the line from the COUNT on a throwaway copy at the "
            "measurement site instead, and leave the delete at the head"
        )

        # POSITIVE CONTROL, in the same fixture. The absence above must be
        # caused by the ANCHOR, and not by a shape that matches nothing at all.
        at_head = _ONE_WARNING_LINE + "a note the user wrote\n"
        assert _LEADING_BUDGET_WARNING_RE.search(at_head) is not None, (
            "the deleting pattern did not find a warning AT the head, so the "
            "negative leg above proves nothing about the anchor"
        )


# ===========================================================================
# User lines survive a pass, and a file keeps its line endings
# ===========================================================================

# THE ORACLE IS COMPOSED HERE FROM THE UNCOMPILED SHAPE, AND IT IS NOT TAKEN
# FROM EITHER COMPILED ANCHOR. The arm below subtracts warning lines from the
# two sides of ONE comparison. An oracle read off a compiled anchor would widen
# that subtraction on BOTH sides together, the extra erasure would cancel, and
# the arm would go GREEN on the mutation it exists to catch. Composed from the
# shape, a change to either anchor cannot move this instrument.
#
# THE RESIDUAL, STATED RATHER THAN LEFT FOR A READER TO FIND. A widening of
# `_BUDGET_WARNING_SHAPE` ITSELF does widen this subtraction, and it does
# cancel. MEASURED: such a widening reddens
# `test_the_corpus_separates_the_two_anchors` instead, so the property is
# guarded by a different arm rather than unguarded. A literal copied into this
# file would close the residual and open a worse one: a renamed constant leaves
# a negative assertion passing for the reason it was written to celebrate.
_WARNING_LINE_RE = re.compile(rf"(?m)^{_BUDGET_WARNING_SHAPE}")

_STALE_MARKER_LINE_RE = re.compile(r"^<!-- STALE: Last relevant \d{4}-\d{2}-\d{2} -->$")


def _survival_fixture():
    """Five documents, each carrying a different way to lose a user line.

    Built fresh on each call, because the bodies derive from the live budget.
    """

    def doc(body):
        return f"# Project Memory\n\n## Pinned Context\n\n{body}\n## Working Memory\nwm\n"

    return {
        # A first pinned line behind whitespace. A `.lstrip()` at the call site
        # eats it, and the compiled objects are untouched, so no anchor gate
        # can see it.
        "indented first pinned line, above budget": doc(
            f"   indented user prose\n### Big\n{over_budget_body()}\n"
        ),
        # Ordinary prose ABOVE a stranded warning. A caller that clears the
        # line before a below-head warning erases this one and leaves the
        # warning count unmoved, so no count assertion reports it.
        "user prose above a stranded warning, above budget": doc(
            f"### Big\nuser prose above the stranded line\n"
            f"{_ONE_WARNING_LINE}{over_budget_body()}\n"
        ),
        # A stale entry, so a STALE marker is inserted and the subtraction of
        # permitted insertions is exercised rather than assumed.
        "an entry with a stale date, above budget": doc(
            f"### Old\nPR #123, merged 2020-01-01\n### Big\n{over_budget_body()}\n"
        ),
        # Whitespace a tidy-up edit would take. An erasure here SETTLES, so the
        # arms that catch a runaway erasure through instability cannot see it.
        "trailing spaces and repeated blank lines": doc(
            f"### Big\nline with trailing spaces   \n\n\n\nlast line\n"
            f"{over_budget_body()}\n"
        ),
        # The quiet member. The pass runs and writes nothing.
        "below budget, with a stranded warning": doc(
            f"### Small\nuser prose\n{_ONE_WARNING_LINE}a few words\n"
        ),
    }


class TestUserLinesSurviveAPass:
    """A pass may add its own lines. It may not take a line the user wrote.

    THE CLOSED LIST OF PERMITTED CHANGES. Over the lines of the file, this
    module may do THREE things and nothing else:
      1. ADD one warning line at the head of the pinned body.
      2. REMOVE the contiguous run of warning lines at that same head.
      3. ADD one STALE marker line after the heading line of a stale entry.
    Every other change to a line is a defect. The arm encodes that list as its
    subtraction, so a later reader can tell an allowance from an oversight.

    WHY THE OTHER ARMS IN THIS FILE CANNOT STAND IN FOR THIS ONE. They are
    keyed on lines that carry the shape THIS MODULE writes. A caller that
    erases bytes of any OTHER shape sits outside their reach, whatever kind of
    assertion they use. That is a bound on their fixtures and their subject,
    and not a weakness in their assertions.

    IT DRIVES DOCUMENTS, AND THAT IS THE WHOLE VALUE. A property over
    `_strip_budget_warnings` alone cannot see a call site, so it passes a
    caller that hands the function a pre-trimmed string. That narrower form was
    built by accident during review and it passed such a caller with nothing
    red to report it.

    IT COMPARES ONE PASS, AND THAT IS THE SECOND HALF OF THE VALUE. It reads no
    second pass and it tests no settling. An erasure that repeats and an
    erasure that settles look the same to it, so it does not inherit the gap
    that an instability check leaves open.

    A CHANGE TO A WARNING LINE IS INVISIBLE HERE, BY CONSTRUCTION AND NOT BY
    OVERSIGHT. The subtraction removes warning lines from the TWO sides, so
    this arm says nothing about a warning added, taken back, or refreshed, at
    the head or anywhere else. Read the closed list above as a description of
    the module, and read the SUBTRACTION as the rule of the arm. The two differ
    on purpose, and the subtraction is wider. A reader who takes the list as
    the rule will predict a red that cannot happen, and file a gap that is not
    there.
    """

    def test_no_user_line_is_lost_or_moved_by_a_pass(self, tmp_path):
        """Each non-warning line survives byte for byte and in order.

        MEASURED BEHAVIOUR, on scratch copies of the committed tree:
          baseline                                 PASS, 4 of 5 documents modified
          call site given a pre-trimmed string     FAIL, on the indented member
          strip widened to reach any warning line  PASS, that is another arm
          trailing whitespace tidied on short lines FAIL, on the whitespace member
          write path neutralised                   FAIL, on the floor below

        The third row is deliberate. A mutation that erases only WARNING lines
        belongs to the arms that are keyed on them, and this arm subtracts
        those lines from the two sides, so it correctly says nothing.

        THE FOURTH ROW IS THE ONE THIS ARM EXISTS FOR, and it needs its bound
        stated with it. That tidy erases user bytes BELOW the head and it
        SETTLES, so the arms that catch a runaway erasure through instability
        cannot see it. Measured: it leaves all 100 shipped arms green. So the
        instrument gap is PROVEN, by a SYNTHETIC mutant. Separately, and it
        does not qualify the first half: NO PLAUSIBLE ORDINARY EDIT has been
        found that reaches it. Nothing in this module motivates a whitespace
        tidy. Both statements are the record, and neither cancels the other.

        WHICH FIXTURE MEMBER SERVES WHICH SHAPE:
          indented first pinned line      a call site that trims the head
          trailing spaces, short line     a tidy that erases below the head
          prose above a stranded warning  a caller that clears the line above
          entry with a stale date         the STALE-marker subtraction
          below budget, stranded warning  the quiet member, no write
        No shipped fixture carries either whitespace shape, which is why this
        one builds its own.
        """
        from staleness import check_pinned_staleness

        modified = 0
        for index, (name, before) in enumerate(_survival_fixture().items()):
            home = tmp_path / f"doc{index}"
            home.mkdir()
            claude_md = home / "CLAUDE.md"
            claude_md.write_text(before, encoding="utf-8")

            check_pinned_staleness(claude_md_path=claude_md)

            after = claude_md.read_text(encoding="utf-8")
            if after != before:
                modified += 1

            kept_before = [
                line
                for line in before.split("\n")
                if not _WARNING_LINE_RE.match(line + "\n")
            ]
            kept_after = [
                line
                for line in after.split("\n")
                if not _WARNING_LINE_RE.match(line + "\n")
                and not _STALE_MARKER_LINE_RE.match(line)
            ]
            if kept_after != kept_before:
                at = next(
                    (
                        i
                        for i, (b, a) in enumerate(zip(kept_before, kept_after))
                        if b != a
                    ),
                    min(len(kept_before), len(kept_after)),
                )
                was = kept_before[at] if at < len(kept_before) else "<end>"
                now = kept_after[at] if at < len(kept_after) else "<end>"
                raise AssertionError(
                    f"a pass changed a user line in {name!r}, first at line "
                    f"{at}: {was[:60]!r} became {now[:60]!r}. This module may "
                    f"only add a warning at the head, take back the run of "
                    f"warnings at the head, and add a STALE marker after a "
                    f"stale heading. Any other line change takes text a user "
                    f"wrote, from a file that is frequently gitignored"
                )

        # NON-VACUITY, AND IT IS A MEASURED FLOOR RATHER THAN AN EQUALITY.
        # Survival holds for a hook that never runs, so this count is what
        # makes the assertions above evidence. The floor is 4, measured on the
        # committed tree.
        #
        # WHY A FLOOR AND NOT `== 4`, because the equality was tried first and
        # it was wrong. A mutation that modifies MORE documents does not weaken
        # the evidence for survival, it strengthens it, so an equality reddens
        # on a change that this arm has no business reporting. MEASURED: a
        # strip widened to reach any warning line takes 5 of 5, and the
        # survival property PASSES there, correctly, because that mutation
        # erases only warning lines. The direction that threatens the evidence
        # is FEWER, and the floor catches it.
        assert modified >= 4, (
            f"only {modified} of 5 fixture documents were modified by the "
            f"pass, against a measured floor of 4. The survival assertions "
            f"above hold for a hook that never runs, so a drop here means they "
            f"have stopped being evidence"
        )


class TestLineEndingsSurviveAPass:
    """A file written with CRLF must keep CRLF across a modifying pass.

    `read_text` applies universal-newline translation, so a CRLF file arrives
    as LF and the write puts LF back. The user sees a whole-file rewrite in
    their diff, and the change is silent.
    """

    def test_crlf_endings_survive_a_modifying_pass(self, tmp_path):
        """The bytes the user chose for line endings are theirs, not ours."""
        from staleness import check_pinned_staleness

        before = (
            "# Project Memory\r\n\r\n## Pinned Context\r\n\r\n"
            f"### Big\r\n{over_budget_body()}\r\n"
            "## Working Memory\r\nwm\r\n"
        ).encode("utf-8")
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_bytes(before)

        check_pinned_staleness(claude_md_path=claude_md)

        after = claude_md.read_bytes()
        # NON-VACUITY FIRST. An unmodified document keeps its line endings for
        # a reason that says nothing about a modifying pass.
        assert after != before, (
            "the pass wrote nothing, so this arm says nothing about what a "
            "MODIFYING pass does to line endings"
        )
        assert after.count(b"\r\n") > 0, (
            "a modifying pass converted a CRLF file to LF. Every line of the "
            "user's file changed, which their diff reports as a whole-file "
            "rewrite of a file they did not edit"
        )


# ===========================================================================
# Citations from staleness.py into this file must still resolve
# ===========================================================================

_STALENESS_SOURCE = Path(__file__).parent.parent / "hooks" / "staleness.py"


class TestContainmentImportSitsAboveItsHandler:
    """The import that binds `ContainmentError` must sit OUTSIDE the try that
    handles it.

    THE DEFECT, IF THE IMPORT GOES BACK INSIDE. Python must evaluate the
    handler when the import raises. The name is unbound at that moment, so the
    ImportError is replaced by `UnboundLocalError: cannot access local variable
    ContainmentError`, and the cause survives only as `__context__`. Nothing is
    written and the hook exits 0, so the whole cost is the ability to read what
    went wrong.

    IT LOOKS LIKE TIDINESS TO MOVE IT BACK. The import serves the try body and
    nothing else, so a future editor pulls it in. That is why a comment alone
    is not enough here, and why this guard reads structure rather than prose.

    IT READS THE AST AND NOT THE TEXT. Line numbers and the spelling of the
    import are both free to move. MEASURED, on scratch copies: a re-ordering of
    the imported names, a split into three separate statements, and a move
    further up the function each leave this GREEN. The pre-fix source, which
    holds the import inside the try, turns it RED.

    THE TRY IS FOUND BY ITS HANDLER, never by position. Measured on the
    committed source: exactly one try in the whole module has a handler naming
    `ContainmentError`. The count is NOT pinned, because a second such try
    would be legitimate and the property holds over all of them.
    """

    @staticmethod
    def _guarded_tries(tree):
        """Every try whose handler names `ContainmentError`."""

        def handles_containment(handler):
            return any(
                isinstance(node, ast.Name) and node.id == "ContainmentError"
                for node in ast.walk(handler)
            )

        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(handles_containment(h) for h in node.handlers)
        ]

    def test_the_containment_import_is_not_inside_the_try_that_handles_it(self):
        """The property, stated over structure rather than over text."""
        tree = ast.parse(_STALENESS_SOURCE.read_text(encoding="utf-8"))
        guarded = self._guarded_tries(tree)

        # NON-VACUITY, AND IT IS THE WHOLE REASON THIS GUARD CAN FAIL AT ALL.
        # The property is "no such import is inside any such try". With NO such
        # try, that holds for free and this arm goes silent for ever. The
        # precedent this technique comes from carries the same control facing
        # the other way, where a missing try would make its own assertion
        # vacuous. MEASURED: rename that handler and this line fires.
        assert guarded, (
            "no try in staleness.py has a handler naming ContainmentError, so "
            "the property below quantifies over an empty set and this guard "
            "reports nothing for ever. Either the handler was renamed, in "
            "which case retarget this guard, or the try was removed, in which "
            "case the hazard is gone and so is the reason for this arm"
        )

        offenders = []
        for node in guarded:
            for statement in node.body:
                for sub in ast.walk(statement):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        bound = [alias.asname or alias.name for alias in sub.names]
                        if "ContainmentError" in bound:
                            offenders.append(sub.lineno)

        assert not offenders, (
            f"an import binding ContainmentError sits INSIDE the try that "
            f"handles it, at line(s) {offenders}. When that import raises, "
            f"Python evaluates the handler against an unbound name, so the "
            f"real ImportError is replaced by an UnboundLocalError and "
            f"survives only as __context__. Move the import ABOVE the try. Do "
            f"not wrap it in a handler of its own, because an ImportError must "
            f"reach the caller"
        )

# THE ALPHABET IS TAKEN FROM WHAT PYTEST COLLECTS, NOT FROM WHAT WE REMEMBER
# CITING. pytest collects `Test`-prefixed CLASSES as well as `test_`-prefixed
# FUNCTIONS, so a pattern covering only the second is blind to half the thing it
# measures. `Test` must be followed by an uppercase letter or an underscore, so
# the ordinary English word "Tests" is not mistaken for a class name.
_CITED_TEST_NAME_RE = re.compile(r"\b(?:test_[A-Za-z0-9_]+|Test[A-Z_][A-Za-z0-9_]*)\b")


def _cited_test_names(source):
    """Return every test symbol `source` names in its own prose.

    A name immediately followed by `.py` is a FILE PATH, not a symbol, and is
    excluded: demanding a definition called `test_staleness` would be a false
    failure. The exclusion is applied after the match rather than inside the
    pattern, because a lookahead lets the regex backtrack to a shorter name and
    invent a citation that was never written.

    A name immediately followed by `*` is a GLOB over a FAMILY of arms, and it
    keeps its star. It is not dropped, because a glob is still a pointer and
    can still dangle. `_unresolved` resolves it by prefix, so a family that
    goes away is reported rather than excused.
    """
    found = set()
    for match in _CITED_TEST_NAME_RE.finditer(source):
        if source[match.end():match.end() + 3] == ".py":
            continue
        star = "*" if source[match.end():match.end() + 1] == "*" else ""
        found.add(match.group(0) + star)
    return found


def _unresolved(cited, defined):
    """Return the cited names that nothing in `defined` satisfies.

    A plain name must be present. A name that ends in `*` is a glob, and it is
    satisfied when SOME defined name carries that prefix, so the family is
    checked rather than waved through.
    """
    missing = set()
    for name in cited:
        if name.endswith("*"):
            if not any(d.startswith(name[:-1]) for d in defined):
                missing.add(name)
        elif name not in defined:
            missing.add(name)
    return sorted(missing)


def _defined_test_names(source):
    """Return every class and function `source` DEFINES, at any nesting depth."""
    return {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _prose_of(source):
    """Return the DOCSTRINGS and COMMENTS of `source`, and nothing else.

    THIS FILE CANNOT BE READ WHOLE THE WAY A SOURCE MODULE CAN. A test module
    holds test names in three places that are not citations at all: a local
    variable that collects probe strings, synthetic names handed to an
    extractor as INPUT DATA, and the definitions themselves. Prose is where a
    citation lives, so prose is the population.

    A GENERAL HAZARD, AND IT IS WHY THE POPULATION IS NARROWED RATHER THAN THE
    EXCEPTIONS LISTED. When a checker grows to read the file that holds it, the
    OWN FIXTURES of that checker become part of its input, because fixtures are
    shaped to look like the thing the checker hunts. Synthetic data built to
    exercise the checker then reads as live content, and the checker reports on
    itself. That self-reference is one the widening CREATES, not one it finds,
    and it arrives as false positives indistinguishable from real findings
    until each one is read. Expect it whenever a population grows to cover the
    checker.
    """
    chunks = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                chunks.append(docstring)
    for line in source.split("\n"):
        hash_at = line.find("#")
        if hash_at != -1:
            chunks.append(line[hash_at:])
    return "\n".join(chunks)


def _defined_across_the_tests_tree():
    """Every test symbol in this directory, plus every test FILE STEM.

    THE RESOLUTION SET IS WIDENED WITH THE POPULATION, rather than the
    population being narrowed by a list of exceptions. A citation to a test in
    a sibling file is legitimate, and so is a reference to a test file by its
    stem, so each must RESOLVE rather than be excluded by a rule somebody has
    to keep current.

    THE COST, STATED. This set is large, so a citation can resolve against an
    unrelated test that happens to carry the name. The names in question are
    long and specific, so the risk is small, and the alternative refuses every
    legitimate cross-file citation. That trade is deliberate.

    THIS GUARD UNDER-DETECTS. A cited name that collides with an unrelated test
    anywhere in the tests tree resolves and passes, so a dangling pointer can
    read as live. Silence from this guard is not proof that each citation
    resolves to what its author meant.

    THE TRADE GOES THIS WAY ON FAILURE DIRECTION. The alternative is a false
    RED on correct prose, in a merge gate. Under-detection that requires a name
    collision beats over-detection that fires on correct writing.
    """
    names = set()
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        names.add(path.stem)
        names |= _defined_test_names(path.read_text(encoding="utf-8"))
    return names


class TestStalenessCitationsResolve:
    """A test name in source prose is a pointer with nothing holding it.

    `staleness.py` cites tests from this file to justify its own behaviour, and
    the most load-bearing of those citations is the one a reader is told to
    check INSTEAD of trusting the author. A rename on this side turns that into
    a dangling pointer, silently, and no existing gate covers it: the twin-parity
    gate protects three named twin-copy tests and nothing else.

    THE CITED SET IS DERIVED, NEVER HAND-LISTED. A hand list protects the names
    somebody remembered to add to it, and its failure mode is silence, which is
    the same defect one level up.

    FAILURE DIRECTION IS DELIBERATE. Over-detection REFUSES and says which name,
    which is loud and cheap to fix. Under-detection is silent and reinstates the
    dangling pointer, so the alphabet is bounded rather than narrowed.
    """

    def test_the_extractor_covers_both_things_pytest_collects(self):
        """Instrument check on a synthetic input, not on the current file.

        Asserting that the LIVE citation set contains both kinds would couple
        this to today's prose and redden when a citation is legitimately
        removed. The alphabet is the thing under test, so the input is fixed.
        """
        found = _cited_test_names("see TestFooBar and test_foo_bar for why")
        assert found == {"TestFooBar", "test_foo_bar"}

    def test_the_extractor_ignores_a_file_path(self):
        """A name with a `.py` suffix is a path. A bare name is a symbol."""
        assert _cited_test_names("see test_staleness.py for details") == set()
        assert _cited_test_names("test_staleness.py defines test_real_thing") == {
            "test_real_thing"
        }
        assert _cited_test_names("Tests cover the module") == set()

    def test_staleness_cites_at_least_one_test(self):
        """NON-VACUITY. An extractor returning nothing satisfies the guard below
        perfectly and for ever, so the population is asserted before the
        property that quantifies over it."""
        cited = _cited_test_names(
            _STALENESS_SOURCE.read_text(encoding="utf-8")
        )
        assert cited, (
            "no test citations were extracted from staleness.py, so the "
            "resolution check below is quantifying over an empty set"
        )

    def test_each_file_supplies_its_own_citations(self):
        """NON-VACUITY FOR THE WIDENED POPULATION, RE-DERIVED RATHER THAN CARRIED.

        The arm above asserts that SOME citation was extracted, and it was
        calibrated when one file supplied them all. Over two files that same
        claim is much weaker: a broken extractor on the TEST file leaves the
        source module's citations in place, the assertion holds, and half the
        population goes silent with nothing to report it.

        So the floor is PER FILE, and each figure is measured rather than
        chosen. If a legitimate edit takes a file below its floor, lower the
        figure and say what went, rather than delete the arm.

        THAT RULE HAS BEEN APPLIED ONCE, WHEN THIS ARM WAS NEW. The committed
        file carried 19 prose citations. Two left for good reasons: a name that
        no test ever defined, and a synthetic name that this file's own
        instrument tests use as INPUT rather than as a pointer. The floor is
        the measured 17 that remains.
        """
        from_source = _cited_test_names(
            _STALENESS_SOURCE.read_text(encoding="utf-8")
        )
        from_this_file = _cited_test_names(
            _prose_of(Path(__file__).read_text(encoding="utf-8"))
        )
        assert len(from_source) >= 3, (
            f"staleness.py supplied {len(from_source)} citations against a "
            f"measured floor of 3, so the source half of this guard has lost "
            f"population it used to carry"
        )
        assert len(from_this_file) >= 17, (
            f"this file's prose supplied {len(from_this_file)} citations "
            f"against a measured floor of 17, so the half of this guard that "
            f"reads its OWN file is quantifying over less than it used to"
        )

    def test_every_test_cited_by_this_file_still_exists(self):
        """The same property, turned on the file that holds the guard.

        WHY THIS ARM EXISTS. The guard above reads the SOURCE module only. The
        file carrying it held a dangling citation about fifteen hundred lines
        below the guard, and the guard could not see it. A checker blind to its
        own file is the shape it was written to refuse.

        THE POPULATION IS PROSE, and the resolution set is the whole tests
        directory. See `_prose_of` and `_defined_across_the_tests_tree` for why
        each is drawn that way.
        """
        cited = _cited_test_names(
            _prose_of(Path(__file__).read_text(encoding="utf-8"))
        )
        missing = _unresolved(cited, _defined_across_the_tests_tree())
        assert not missing, (
            f"this file's prose cites {missing}, and no test in this directory "
            f"defines them. The prose that leans on those names can no longer "
            f"be checked by a reader who follows the pointer"
        )

    def test_every_test_cited_by_staleness_still_exists(self):
        """The property itself."""
        cited = _cited_test_names(
            _STALENESS_SOURCE.read_text(encoding="utf-8")
        )
        defined = _defined_test_names(
            Path(__file__).read_text(encoding="utf-8")
        )
        missing = _unresolved(cited, defined)
        assert not missing, (
            f"staleness.py cites {missing} but this file no longer defines "
            f"them; the citation is a dangling pointer and the prose that "
            f"leans on it can no longer be checked"
        )

    def test_the_guard_detects_a_deleted_citation(self):
        """MUTATION ARM. Proves the check reddens on the failure it exists for,
        by removing a cited name from the defined set rather than by trusting
        that a set difference must work."""
        cited = _cited_test_names(
            _STALENESS_SOURCE.read_text(encoding="utf-8")
        )
        defined = _defined_test_names(
            Path(__file__).read_text(encoding="utf-8")
        )
        victim = sorted(cited)[0]
        assert not (cited - defined), "fixture invalid: the real check is red"
        assert (cited - (defined - {victim})) == {victim}, (
            "deleting a cited definition did not surface it as missing"
        )
