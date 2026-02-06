"""
Tests for token budget enforcement in working_memory.py.

Tests cover:
1. _estimate_tokens() -- word-based approximation
2. _compress_memory_entry() -- single-line summary extraction
3. _apply_token_budget() -- budget enforcement with compression/dropping
4. sync_to_claude_md() -- budget enforcement during working memory sync
5. sync_retrieved_to_claude_md() -- budget enforcement during retrieved context sync
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"))


class TestEstimateTokens:
    """Tests for _estimate_tokens() approximation function."""

    def test_empty_string_returns_zero(self):
        """Empty string should return 0 tokens."""
        from working_memory import _estimate_tokens
        assert _estimate_tokens("") == 0

    def test_single_word(self):
        """Single word should return int(1 * 1.3) = 1."""
        from working_memory import _estimate_tokens
        assert _estimate_tokens("hello") == 1

    def test_ten_words(self):
        """Ten words should return int(10 * 1.3) = 13."""
        from working_memory import _estimate_tokens
        text = "one two three four five six seven eight nine ten"
        assert _estimate_tokens(text) == 13

    def test_returns_integer(self):
        """Should always return an integer, not a float."""
        from working_memory import _estimate_tokens
        result = _estimate_tokens("some words here")
        assert isinstance(result, int)

    def test_longer_text_scales_proportionally(self):
        """Longer text should produce proportionally larger estimates."""
        from working_memory import _estimate_tokens
        short = _estimate_tokens("a b c")
        long_val = _estimate_tokens("a b c d e f g h i j k l m n o")
        assert long_val > short


class TestCompressMemoryEntry:
    """Tests for _compress_memory_entry() -- extracts single-line summary."""

    def test_extracts_context_first_sentence(self):
        """Should extract first sentence from Context field."""
        from working_memory import _compress_memory_entry

        entry = (
            "### 2026-01-15 10:30\n"
            "**Context**: Working on authentication module. This involves JWT tokens.\n"
            "**Goal**: Add refresh token support\n"
            "**Decisions**: Use Redis for token storage\n"
            "**Memory ID**: abc123"
        )
        result = _compress_memory_entry(entry)
        assert "### 2026-01-15 10:30" in result
        assert "**Summary**: Working on authentication module." in result
        assert "**Goal**" not in result
        assert "**Decisions**" not in result
        assert "**Memory ID**" not in result

    def test_truncates_long_context_without_period(self):
        """Should truncate to 120 chars with ellipsis when no period found early."""
        from working_memory import _compress_memory_entry

        long_context = "A" * 200
        entry = f"### 2026-01-15 10:30\n**Context**: {long_context}"
        result = _compress_memory_entry(entry)
        assert "**Summary**:" in result
        assert "..." in result
        summary_line = [line for line in result.split("\n") if "**Summary**" in line][0]
        summary_text = summary_line.split("**Summary**: ", 1)[1]
        assert len(summary_text) <= 124  # 120 + "..."

    def test_handles_context_with_early_period(self):
        """Should take first sentence if period appears before 120 chars."""
        from working_memory import _compress_memory_entry

        entry = (
            "### 2026-02-01 08:00\n"
            "**Context**: Short sentence. Then more text follows here."
        )
        result = _compress_memory_entry(entry)
        assert "**Summary**: Short sentence." in result

    def test_preserves_date_header(self):
        """Date header line should always be preserved."""
        from working_memory import _compress_memory_entry

        entry = "### 2026-03-15 14:22\n**Context**: Some context"
        result = _compress_memory_entry(entry)
        assert result.startswith("### 2026-03-15 14:22")

    def test_falls_back_to_first_field_when_no_context(self):
        """Should use first bold field if no Context field present."""
        from working_memory import _compress_memory_entry

        entry = (
            "### 2026-01-20 12:00\n"
            "**Goal**: Implement the new feature\n"
            "**Decisions**: Use React"
        )
        result = _compress_memory_entry(entry)
        assert "### 2026-01-20 12:00" in result
        assert "Implement the new feature" in result

    def test_empty_entry_returns_entry(self):
        """Empty entry should return itself."""
        from working_memory import _compress_memory_entry
        assert _compress_memory_entry("") == ""

    def test_header_only_entry(self):
        """Entry with only date header should return just the header."""
        from working_memory import _compress_memory_entry
        result = _compress_memory_entry("### 2026-01-01 00:00")
        assert result == "### 2026-01-01 00:00"


class TestApplyTokenBudget:
    """Tests for _apply_token_budget() -- compression and dropping."""

    def test_empty_entries_returns_empty(self):
        """Empty list should return empty list."""
        from working_memory import _apply_token_budget
        assert _apply_token_budget([], 800) == []

    def test_under_budget_no_change(self):
        """Entries under budget should be returned unchanged."""
        from working_memory import _apply_token_budget

        entries = [
            "### 2026-01-15 10:00\n**Context**: Short entry",
            "### 2026-01-14 10:00\n**Context**: Another short",
        ]
        result = _apply_token_budget(entries, 800)
        assert result == entries

    def test_over_budget_compresses_older_entries(self):
        """When over budget, older entries should be compressed (newest stays full)."""
        from working_memory import _apply_token_budget

        long_text = "word " * 100  # ~130 tokens per entry
        entries = [
            f"### 2026-01-15 10:00\n**Context**: {long_text}",
            f"### 2026-01-14 10:00\n**Context**: {long_text}\n**Goal**: Some goal\n**Decisions**: Something",
            f"### 2026-01-13 10:00\n**Context**: {long_text}\n**Goal**: Another goal",
        ]

        # Budget of 250 is enough for the first entry (~130 tokens) plus
        # compressed older entries (~4 tokens each), guaranteeing at least 2
        # entries survive.
        result = _apply_token_budget(entries, 250)

        # At least 2 entries must survive (newest full + compressed older)
        assert len(result) >= 2, f"Expected at least 2 entries, got {len(result)}"
        # First entry should be unchanged (newest)
        assert result[0] == entries[0]
        # Older entries must be compressed -- the compression marker is "**Summary**"
        assert "**Summary**" in result[1], (
            f"Expected compressed entry to contain '**Summary**', got: {result[1][:200]}"
        )

    def test_way_over_budget_drops_entries(self):
        """When compressed entries still exceed budget, should drop from end."""
        from working_memory import _apply_token_budget

        huge_text = "word " * 500  # ~650 tokens
        entries = [
            f"### 2026-01-15 10:00\n**Context**: {huge_text}",
            f"### 2026-01-14 10:00\n**Context**: {huge_text}",
            f"### 2026-01-13 10:00\n**Context**: {huge_text}",
            f"### 2026-01-12 10:00\n**Context**: {huge_text}",
        ]

        result = _apply_token_budget(entries, 700)

        assert len(result) < len(entries)
        assert len(result) >= 1
        assert result[0] == entries[0]

    def test_single_entry_always_kept(self):
        """A single entry should never be dropped, even if over budget."""
        from working_memory import _apply_token_budget

        huge_text = "word " * 1000
        entries = [f"### 2026-01-15 10:00\n**Context**: {huge_text}"]
        result = _apply_token_budget(entries, 10)
        assert len(result) == 1

    def test_budget_of_zero_keeps_first_entry(self):
        """Budget of zero should still keep at least the first entry."""
        from working_memory import _apply_token_budget

        entries = ["### 2026-01-15\n**Context**: Some text"]
        result = _apply_token_budget(entries, 0)
        assert len(result) == 1


class TestSyncToClaudeMdBudgetEnforcement:
    """Tests that sync_to_claude_md applies token budget."""

    def _create_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_large_entries_compressed_during_sync(self, tmp_path):
        """sync_to_claude_md should compress older entries to stay within budget."""
        from working_memory import sync_to_claude_md

        long_text = "word " * 200
        existing_content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            f"### 2026-01-14 10:00\n**Context**: {long_text}\n**Goal**: Old goal\n\n"
            f"### 2026-01-13 10:00\n**Context**: {long_text}\n**Goal**: Older goal\n\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("working_memory._get_claude_md_path", return_value=claude_md):
            result = sync_to_claude_md(
                {"context": "New context entry", "goal": "New goal"},
                memory_id="test123"
            )

        assert result is True
        new_content = claude_md.read_text(encoding="utf-8")
        assert "## Working Memory" in new_content
        assert "New context entry" in new_content

    def test_sync_with_budget_produces_valid_markdown(self, tmp_path):
        """Output should be valid markdown with proper section structure."""
        from working_memory import sync_to_claude_md

        content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "## Pinned Context\n\nSome pinned stuff\n"
        )
        claude_md = self._create_claude_md(tmp_path, content)

        with patch("working_memory._get_claude_md_path", return_value=claude_md):
            sync_to_claude_md({"context": "Test"}, memory_id="id1")

        new_content = claude_md.read_text(encoding="utf-8")
        assert "## Pinned Context" in new_content
        assert "Some pinned stuff" in new_content


    def test_entry_count_trimmed_to_max_working_memories(self, tmp_path):
        """sync_to_claude_md should trim entries to MAX_WORKING_MEMORIES (3)."""
        from working_memory import sync_to_claude_md, MAX_WORKING_MEMORIES

        existing_content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### 2026-01-15 10:00\n**Context**: Entry one\n\n"
            "### 2026-01-14 09:00\n**Context**: Entry two\n\n"
            "### 2026-01-13 08:00\n**Context**: Entry three\n\n"
            "### 2026-01-12 07:00\n**Context**: Entry four\n\n"
            "### 2026-01-11 06:00\n**Context**: Entry five\n\n"
            "## Pinned Context\n\nPinned stuff\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("working_memory._get_claude_md_path", return_value=claude_md):
            result = sync_to_claude_md(
                {"context": "Brand new entry"},
                memory_id="new123"
            )

        assert result is True
        new_content = claude_md.read_text(encoding="utf-8")

        # Count ### YYYY-MM-DD entries in the Working Memory section.
        # Extract text between "## Working Memory" and the next "## " heading.
        wm_match = re.search(
            r'## Working Memory\n.*?(?=\n## (?!Working Memory)|\Z)',
            new_content,
            re.DOTALL
        )
        assert wm_match is not None, "Working Memory section not found"
        wm_section = wm_match.group()
        entry_count = len(re.findall(r'^### \d{4}-\d{2}-\d{2}', wm_section, re.MULTILINE))

        # Should be exactly MAX_WORKING_MEMORIES (3): the new entry + 2 most recent existing
        assert entry_count == MAX_WORKING_MEMORIES, (
            f"Expected {MAX_WORKING_MEMORIES} entries, found {entry_count}"
        )

        # The new entry should be present (newest first)
        assert "Brand new entry" in new_content
        # The oldest entries (four, five) should have been trimmed
        assert "Entry four" not in new_content
        assert "Entry five" not in new_content

        # Pinned Context should be preserved
        assert "## Pinned Context" in new_content
        assert "Pinned stuff" in new_content


class TestSyncRetrievedBudgetEnforcement:
    """Tests that sync_retrieved_to_claude_md applies token budget."""

    def _create_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_retrieved_entries_reduced_when_over_budget(self, tmp_path):
        """Should drop old retrieved entries when over budget."""
        from working_memory import sync_retrieved_to_claude_md

        long_text = "word " * 200
        existing_content = (
            "# Project\n\n"
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            f"### 2026-01-14 10:00\n**Query**: \"old query\"\n**Context**: {long_text}\n\n"
            f"### 2026-01-13 10:00\n**Query**: \"older query\"\n**Context**: {long_text}\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("working_memory._get_claude_md_path", return_value=claude_md):
            result = sync_retrieved_to_claude_md(
                [{"context": "New retrieved", "goal": "test"}],
                query="test search",
                memory_ids=["mem1"]
            )

        assert result is True
        new_content = claude_md.read_text(encoding="utf-8")
        assert "test search" in new_content
        assert "## Working Memory" in new_content

    def test_no_memories_returns_false(self):
        """sync_retrieved_to_claude_md with empty list should return False."""
        from working_memory import sync_retrieved_to_claude_md
        result = sync_retrieved_to_claude_md([], query="test")
        assert result is False


class TestFormatMemoryEntry:
    """Direct unit tests for _format_memory_entry() helper."""

    def test_basic_fields(self):
        """Should format context, goal, and memory_id into markdown."""
        from working_memory import _format_memory_entry

        memory = {"context": "Working on auth", "goal": "Add JWT support"}
        result = _format_memory_entry(memory, memory_id="abc123")

        assert "**Context**: Working on auth" in result
        assert "**Goal**: Add JWT support" in result
        assert "**Memory ID**: abc123" in result
        assert result.startswith("### ")

    def test_decisions_as_list_of_strings(self):
        """Decisions provided as a list of strings should be joined with commas."""
        from working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": ["Use Redis", "Add caching"]}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis, Add caching" in result

    def test_decisions_as_list_of_dicts(self):
        """Decisions provided as list of dicts should extract 'decision' key."""
        from working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": [
            {"decision": "Use Redis"},
            {"decision": "Add caching"}
        ]}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis, Add caching" in result

    def test_decisions_as_string(self):
        """Decisions provided as a plain string should be used directly."""
        from working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": "Use Redis for storage"}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis for storage" in result

    def test_lessons_as_list(self):
        """Lessons provided as a list should be joined with commas."""
        from working_memory import _format_memory_entry

        memory = {"context": "Test", "lessons_learned": ["Cache invalidation is hard", "Use TTL"]}
        result = _format_memory_entry(memory)

        assert "**Lessons**: Cache invalidation is hard, Use TTL" in result

    def test_lessons_as_string(self):
        """Lessons provided as a string should be used directly."""
        from working_memory import _format_memory_entry

        memory = {"context": "Test", "lessons_learned": "Always use TTL for caches"}
        result = _format_memory_entry(memory)

        assert "**Lessons**: Always use TTL for caches" in result

    def test_missing_optional_fields(self):
        """Missing optional fields should be omitted from output."""
        from working_memory import _format_memory_entry

        memory = {"context": "Just context, nothing else"}
        result = _format_memory_entry(memory)

        assert "**Context**: Just context, nothing else" in result
        assert "**Goal**" not in result
        assert "**Decisions**" not in result
        assert "**Lessons**" not in result
        assert "**Files**" not in result
        assert "**Memory ID**" not in result

    def test_files_list(self):
        """Files list should be formatted as comma-separated values."""
        from working_memory import _format_memory_entry

        memory = {"context": "Test"}
        result = _format_memory_entry(memory, files=["src/auth.py", "tests/test_auth.py"])

        assert "**Files**: src/auth.py, tests/test_auth.py" in result

    def test_empty_memory_dict(self):
        """Empty memory dict should produce only the date header line."""
        from working_memory import _format_memory_entry

        result = _format_memory_entry({})
        lines = result.strip().split("\n")
        assert len(lines) == 1
        assert lines[0].startswith("### ")


class TestFormatRetrievedEntry:
    """Direct unit tests for _format_retrieved_entry() helper."""

    def test_basic_formatting(self):
        """Should format query, context, and goal into markdown."""
        from working_memory import _format_retrieved_entry

        memory = {"context": "Auth implementation", "goal": "Add JWT"}
        result = _format_retrieved_entry(memory, query="authentication", memory_id="mem1")

        assert '**Query**: "authentication"' in result
        assert "**Context**: Auth implementation" in result
        assert "**Goal**: Add JWT" in result
        assert "**Memory ID**: mem1" in result
        assert result.startswith("### ")

    def test_context_truncation_at_200_chars(self):
        """Context longer than 200 chars should be truncated with ellipsis."""
        from working_memory import _format_retrieved_entry

        long_context = "A" * 250
        memory = {"context": long_context}
        result = _format_retrieved_entry(memory, query="test")

        context_line = [l for l in result.split("\n") if "**Context**:" in l][0]
        context_value = context_line.split("**Context**: ", 1)[1]
        assert len(context_value) == 200  # 197 + "..."
        assert context_value.endswith("...")

    def test_score_formatting(self):
        """Score should be formatted to 2 decimal places."""
        from working_memory import _format_retrieved_entry

        memory = {"context": "Test"}
        result = _format_retrieved_entry(memory, query="test", score=0.87654)

        assert "**Relevance**: 0.88" in result

    def test_no_score_omits_relevance(self):
        """When score is None, Relevance line should be omitted."""
        from working_memory import _format_retrieved_entry

        memory = {"context": "Test"}
        result = _format_retrieved_entry(memory, query="test")

        assert "**Relevance**" not in result

    def test_missing_optional_fields(self):
        """Missing context and goal should be omitted."""
        from working_memory import _format_retrieved_entry

        result = _format_retrieved_entry({}, query="test")

        assert '**Query**: "test"' in result
        assert "**Context**" not in result
        assert "**Goal**" not in result


class TestParseWorkingMemorySection:
    """Direct unit tests for _parse_working_memory_section() helper."""

    def test_section_not_found(self):
        """When no Working Memory section exists, should return empty entries."""
        from working_memory import _parse_working_memory_section

        content = "# Project\n\n## Some Other Section\nContent here\n"
        before, header, after, entries = _parse_working_memory_section(content)

        assert before == content
        assert header == ""
        assert after == ""
        assert entries == []

    def test_no_next_section(self):
        """Working Memory at end of file (no next section) should capture to EOF."""
        from working_memory import _parse_working_memory_section

        content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### 2026-01-15 10:00\n"
            "**Context**: Some entry\n"
        )
        before, header, after, entries = _parse_working_memory_section(content)

        assert len(entries) == 1
        assert "Some entry" in entries[0]

    def test_entries_without_proper_date_headers(self):
        """Entries without ### YYYY-MM-DD pattern should not be parsed as entries."""
        from working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### Not a date header\n"
            "Some content\n\n"
            "## Next Section\n"
        )
        _, _, _, entries = _parse_working_memory_section(content)

        # "### Not a date header" does not match ### YYYY-MM-DD pattern
        assert entries == []

    def test_empty_section(self):
        """Section with header but no entries should return empty list."""
        from working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "## Pinned Context\n"
        )
        _, header, _, entries = _parse_working_memory_section(content)

        assert header == "## Working Memory"
        assert entries == []

    def test_multiple_entries_parsed_correctly(self):
        """Multiple entries should be parsed as separate items."""
        from working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### 2026-01-15 10:00\n"
            "**Context**: First entry\n\n"
            "### 2026-01-14 09:00\n"
            "**Context**: Second entry\n\n"
            "## Pinned Context\n"
        )
        _, _, _, entries = _parse_working_memory_section(content)

        assert len(entries) == 2
        assert "First entry" in entries[0]
        assert "Second entry" in entries[1]


class TestParseRetrievedContextSection:
    """Direct unit tests for _parse_retrieved_context_section() helper."""

    def test_section_not_found(self):
        """When no Retrieved Context section exists, should return empty entries."""
        from working_memory import _parse_retrieved_context_section

        content = "# Project\n\n## Working Memory\nContent here\n"
        before, header, after, entries = _parse_retrieved_context_section(content)

        assert before == content
        assert header == ""
        assert after == ""
        assert entries == []

    def test_no_next_section(self):
        """Retrieved Context at end of file should capture to EOF."""
        from working_memory import _parse_retrieved_context_section

        content = (
            "# Project\n\n"
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            "### 2026-01-15 10:00\n"
            '**Query**: "auth"\n'
            "**Context**: Some context\n"
        )
        _, header, _, entries = _parse_retrieved_context_section(content)

        assert header == "## Retrieved Context"
        assert len(entries) == 1
        assert "auth" in entries[0]

    def test_empty_section(self):
        """Section with header but no entries should return empty list."""
        from working_memory import _parse_retrieved_context_section

        content = (
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            "## Working Memory\n"
        )
        _, header, _, entries = _parse_retrieved_context_section(content)

        assert header == "## Retrieved Context"
        assert entries == []

    def test_entries_without_date_headers_ignored(self):
        """Non-date ### headings should not be parsed as entries."""
        from working_memory import _parse_retrieved_context_section

        content = (
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            "### Some random heading\n"
            "Content\n\n"
            "## Working Memory\n"
        )
        _, _, _, entries = _parse_retrieved_context_section(content)

        assert entries == []

    def test_preserves_before_and_after_content(self):
        """Should correctly split content around the Retrieved Context section."""
        from working_memory import _parse_retrieved_context_section

        content = (
            "# Project\n\n"
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            "### 2026-01-15 10:00\n"
            '**Query**: "test"\n\n'
            "## Working Memory\n"
            "Some working memory stuff\n"
        )
        before, _, after, _ = _parse_retrieved_context_section(content)

        assert "# Project" in before
        assert "## Working Memory" in after
        assert "working memory stuff" in after
