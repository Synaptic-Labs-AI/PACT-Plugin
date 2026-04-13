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


class TestParsePinnedSectionMarkerBoundary:
    r"""Direct unit tests for `staleness._parse_pinned_section`'s marker-aware
    section-end detection.

    Round-4 Item 4: the integration test `TestCheckPinnedStaleness.test_pinned_content_before_memory_end_marker`
    passes even if the next_section regex is relaxed back to `^#{1,2}\s`
    because the fixture content has no stale entries, so no write-back occurs
    and the marker is never touched. A unit-level test on `_parse_pinned_section`
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

    def test_symlink_target_rejected_with_opaque_status(self, tmp_path):
        """If the project CLAUDE.md is a symlink, check_pinned_staleness
        returns an opaque 'precondition not met' string and does NOT touch
        the symlink target.

        Pre-fix, the function would call write_text on the symlink path,
        which would follow the link and clobber the attacker-chosen target.
        Post-fix, the inside-lock symlink guard refuses to operate.
        """
        from session_init import check_pinned_staleness

        # Plant a regular file as the symlink target
        symlink_target = tmp_path / "external_target.md"
        original_target_content = "# External file (must not be modified)\n"
        symlink_target.write_text(original_target_content, encoding="utf-8")

        # Replace the project CLAUDE.md with a symlink to the external target
        managed_path = tmp_path / "CLAUDE.md"
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        # The symlink itself reads through to stale content (so the inner
        # read_text + parse + apply_staleness_markings path runs and
        # produces modified=True, which is what gates the file_lock entry)
        symlink_target.write_text(
            self._stale_pinned_content(), encoding="utf-8"
        )

        with patch("session_init._get_project_claude_md_path", return_value=managed_path), \
             patch("staleness._get_project_claude_md_path", return_value=managed_path):
            result = check_pinned_staleness()

        # Status string is opaque — does not reveal the internal symlink
        # check to a local attacker reading hook stderr.
        assert result is not None
        assert "skipped" in result.lower()
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()

        # Critical: the symlink TARGET is byte-identical to what we wrote
        # last (the stale content). The function did not append a STALE
        # marker, did not insert a budget warning, did not write at all.
        assert symlink_target.read_text(encoding="utf-8") == self._stale_pinned_content()
        # The symlink itself is still a symlink (was not replaced with a
        # regular file by a bypassing write).
        assert managed_path.is_symlink()

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

        # Track writes so we can assert no write occurred
        write_calls = []
        original_write_text = Path.write_text

        def tracking_write_text(self, content, *args, **kwargs):
            if str(self) == str(claude_md):
                write_calls.append(content)
            return original_write_text(self, content, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        monkeypatch.setattr(Path, "write_text", tracking_write_text)

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
            f"re-read guard at staleness.py L386-388 is not protecting the "
            f"concurrent writer's content."
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


class TestParsePinnedSectionFenceAware:
    """Round 5 item 4: `_parse_pinned_section` must track code-fence state
    so that a fenced block inside the Pinned Context section containing a
    line that looks like a section terminator (H2 heading, PACT boundary
    marker) does NOT prematurely terminate the section.

    Rationale: pinned prose routinely contains triple-backtick examples
    (tutorials, config snippets, JSON blocks, quoted markers). Without
    fence tracking the parser would truncate the pinned region at the
    first fenced-block pseudo-terminator, and downstream write-back
    operations would eat everything between the fake terminator and the
    real terminator. This is the same failure mode that
    `_parse_working_memory_section` guards against — staleness parses
    prose content authored by both plugin and user, so fence tracking
    is required here too.
    """

    def test_fenced_h2_does_not_terminate_pinned_section(self):
        """An H2 heading inside a fenced code block must not be treated
        as a section terminator.
        """
        from staleness import _parse_pinned_section

        content = (
            "# Project Memory\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "### Real pin entry\n"
            "Explaining the section format:\n"
            "\n"
            "```markdown\n"
            "## Not a real heading\n"
            "This H2 lives inside a fence and must be ignored.\n"
            "```\n"
            "\n"
            "More prose after the fence.\n"
            "\n"
            "## Working Memory\n"
            "Real terminator — Working Memory follows Pinned Context.\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _pinned_start, _pinned_end, pinned_content = result

        # The fenced fake H2 must be inside the extracted region
        assert "## Not a real heading" in pinned_content
        assert "More prose after the fence." in pinned_content
        # The real terminator must NOT be inside the extracted region
        assert "Real terminator" not in pinned_content

    def test_fenced_pact_marker_does_not_terminate_pinned_section(self):
        """A PACT boundary marker inside a fenced code block must not
        terminate the section.
        """
        from staleness import _parse_pinned_section

        content = (
            "# Project Memory\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "### Documentation pin\n"
            "Example of the managed-boundary marker:\n"
            "\n"
            "```html\n"
            "<!-- PACT_MEMORY_END -->\n"
            "```\n"
            "\n"
            "Further narrative.\n"
            "\n"
            "<!-- PACT_MEMORY_END -->\n"
            "## After memory block\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _pinned_start, pinned_end, pinned_content = result

        # The fenced fake marker must be inside the extracted region
        assert "```html" in pinned_content
        assert "Further narrative." in pinned_content
        # The real marker at the bottom must be the terminator — check
        # that pinned_end points to the real marker's line.
        assert content[pinned_end:].lstrip().startswith("<!-- PACT_MEMORY_END -->")

    def test_unfenced_pact_marker_still_terminates_pinned_section(self):
        """Regression guard: the fence-aware refactor must not weaken
        the existing terminator behavior for unfenced markers.
        """
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

    def test_nested_fences_balance_correctly(self):
        """Two separate fenced blocks in sequence: the parser must
        properly toggle state twice, not get stuck in one.
        """
        from staleness import _parse_pinned_section

        content = (
            "## Pinned Context\n"
            "\n"
            "### Two examples\n"
            "\n"
            "```\n"
            "## fake heading 1\n"
            "```\n"
            "\n"
            "Between fences.\n"
            "\n"
            "```\n"
            "## fake heading 2\n"
            "```\n"
            "\n"
            "After both fences.\n"
            "\n"
            "## Working Memory\n"
        )

        result = _parse_pinned_section(content)
        assert result is not None
        _pinned_start, _pinned_end, pinned_content = result

        # Both fake headings must survive
        assert "## fake heading 1" in pinned_content
        assert "## fake heading 2" in pinned_content
        assert "Between fences." in pinned_content
        assert "After both fences." in pinned_content
        # The real terminator must be excluded
        assert "Working Memory" not in pinned_content
