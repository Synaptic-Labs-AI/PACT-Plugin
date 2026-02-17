"""
Tests for check_pinned_staleness() in session_init.py.

Tests cover:
1. No pinned context section -- no-op
2. Pinned context with recent PR -- not flagged
3. Pinned context with old PR -- flagged stale
4. Pinned context without PR dates -- skipped
5. Over budget -- warning comment added
6. Under budget -- no warning
7. Already-marked stale entries -- not double-marked
8. Multiple entries with mixed staleness
9. _estimate_tokens twin copy equivalence (staleness.py vs working_memory.py)
"""

import inspect
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
# Add working_memory scripts directory to path for twin-copy equivalence test
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"))


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
             patch("staleness._get_project_claude_md_path", return_value=None):
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
        from session_init import check_pinned_staleness, PINNED_CONTEXT_TOKEN_BUDGET

        # Create a lot of pinned content that exceeds the budget
        big_content = "word " * 1500  # Should exceed 1200 token budget

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
        assert "<!-- WARNING: Pinned context" in content

    def test_under_budget_no_warning(self, tmp_path):
        """Should not add warning when pinned content is within budget."""
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
        assert "<!-- WARNING: Pinned context" not in content

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

        big_content = "word " * 1500  # Exceeds 1200 token budget

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
        warning_count = content_after.count("<!-- WARNING: Pinned context")
        assert warning_count == 1, (
            f"Expected exactly 1 budget warning comment, found {warning_count}"
        )


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

    def test_read_text_unicode_decode_error_returns_none(self, tmp_path):
        """UnicodeDecodeError on read_text() should return None gracefully."""
        from staleness import check_pinned_staleness

        claude_md = self._create_claude_md(tmp_path, "# Project\n")

        error = UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
        with patch.object(type(claude_md), "read_text", side_effect=error):
            result = check_pinned_staleness(claude_md_path=claude_md)

        assert result is None

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

        # Let read_text work normally, but make write_text fail
        with patch.object(type(claude_md), "write_text", side_effect=IOError("read-only fs")):
            result = check_pinned_staleness(claude_md_path=claude_md)

        # Should return an error message string (not None)
        assert result is not None
        assert "Failed to update pinned staleness" in result
        assert "read-only fs" in result

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

        with patch.object(type(claude_md), "write_text", side_effect=OSError("permission denied")):
            result = check_pinned_staleness(claude_md_path=claude_md)

        assert result is not None
        assert "Failed to update pinned staleness" in result


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
        from working_memory import _estimate_tokens as working_memory_fn

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
        from working_memory import _estimate_tokens as working_memory_fn

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
        from working_memory import _estimate_tokens as working_memory_fn

        assert staleness_fn("") == 0
        assert working_memory_fn("") == 0
        assert staleness_fn("") == working_memory_fn("")

    def test_both_produce_same_result(self):
        """Both copies must produce identical results for the same input."""
        from staleness import estimate_tokens as staleness_fn
        from working_memory import _estimate_tokens as working_memory_fn

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
