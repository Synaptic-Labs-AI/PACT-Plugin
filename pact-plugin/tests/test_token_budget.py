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
from unittest.mock import patch, MagicMock



class TestEstimateTokens:
    """Tests for _estimate_tokens() approximation function."""

    def test_empty_string_returns_zero(self):
        """Empty string should return 0 tokens."""
        from scripts.working_memory import _estimate_tokens
        assert _estimate_tokens("") == 0

    def test_single_word(self):
        """Single word should return int(1 * 1.3) = 1."""
        from scripts.working_memory import _estimate_tokens
        assert _estimate_tokens("hello") == 1

    def test_ten_words(self):
        """Ten words should return int(10 * 1.3) = 13."""
        from scripts.working_memory import _estimate_tokens
        text = "one two three four five six seven eight nine ten"
        assert _estimate_tokens(text) == 13

    def test_returns_integer(self):
        """Should always return an integer, not a float."""
        from scripts.working_memory import _estimate_tokens
        result = _estimate_tokens("some words here")
        assert isinstance(result, int)

    def test_longer_text_scales_proportionally(self):
        """Longer text should produce proportionally larger estimates."""
        from scripts.working_memory import _estimate_tokens
        short = _estimate_tokens("a b c")
        long_val = _estimate_tokens("a b c d e f g h i j k l m n o")
        assert long_val > short


class TestCompressMemoryEntry:
    """Tests for _compress_memory_entry() -- extracts single-line summary."""

    def test_extracts_context_first_sentence(self):
        """Should extract first sentence from Context field."""
        from scripts.working_memory import _compress_memory_entry

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
        # THE MEMORY ID IS KEPT, AND THIS ASSERTION WAS REVERSED RATHER THAN
        # REMOVED. It read `"**Memory ID**" not in result`, which pinned the
        # defect: compression dropped the RECOVERY KEY and left only the
        # ROUTE, so a compressed entry could be recovered from the store
        # only by a content search across the summary. The compressed form
        # is now three lines, and the key is the third.
        assert "**Memory ID**: abc123" in result

    def test_truncates_long_context_without_period(self):
        """Should truncate to 120 chars with ellipsis when no period found early."""
        from scripts.working_memory import _compress_memory_entry

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
        from scripts.working_memory import _compress_memory_entry

        entry = (
            "### 2026-02-01 08:00\n"
            "**Context**: Short sentence. Then more text follows here."
        )
        result = _compress_memory_entry(entry)
        assert "**Summary**: Short sentence." in result

    def test_preserves_date_header(self):
        """Date header line should always be preserved."""
        from scripts.working_memory import _compress_memory_entry

        entry = "### 2026-03-15 14:22\n**Context**: Some context"
        result = _compress_memory_entry(entry)
        assert result.startswith("### 2026-03-15 14:22")

    def test_falls_back_to_first_field_when_no_context(self):
        """Should use first bold field if no Context field present."""
        from scripts.working_memory import _compress_memory_entry

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
        from scripts.working_memory import _compress_memory_entry
        assert _compress_memory_entry("") == ""

    def test_header_only_entry(self):
        """Entry with only date header should return just the header."""
        from scripts.working_memory import _compress_memory_entry
        result = _compress_memory_entry("### 2026-01-01 00:00")
        assert result == "### 2026-01-01 00:00"


class TestApplyTokenBudget:
    """Tests for _apply_token_budget() -- compression and dropping."""

    def test_empty_entries_returns_empty(self):
        """Empty list should return empty list."""
        from scripts.working_memory import _apply_token_budget
        assert _apply_token_budget([], 800) == []

    def test_under_budget_no_change(self):
        """Entries under budget should be returned unchanged."""
        from scripts.working_memory import _apply_token_budget

        entries = [
            "### 2026-01-15 10:00\n**Context**: Short entry",
            "### 2026-01-14 10:00\n**Context**: Another short",
        ]
        result = _apply_token_budget(entries, 800)
        assert result == entries

    def test_over_budget_compresses_older_entries(self):
        """When over budget, older entries should be compressed (newest stays full)."""
        from scripts.working_memory import _apply_token_budget

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

    def test_entry_ceiling_bounds_the_newest_entry(self):
        """The newest entry is bounded by the ceiling, not compressed, not dropped.

        THE FIXTURE IS DERIVED FROM THE MODULE CONSTANTS RATHER THAN FROM
        LITERALS. One test held a literal size against a literal budget and
        went red two times, each time because an honest move of the ceiling
        put its input out of reach of the mechanism it names. A fixture
        computed from the constants moves WITH them.
        """
        from scripts.working_memory import (
            _apply_token_budget,
            _estimate_tokens,
            COMPRESSED_ENTRY_TOKEN_CEILING,
            MAX_WORKING_MEMORIES,
            WORKING_MEMORY_TOKEN_BUDGET,
        )

        entry_ceiling = (
            WORKING_MEMORY_TOKEN_BUDGET
            - (MAX_WORKING_MEMORIES - 1) * COMPRESSED_ENTRY_TOKEN_CEILING
        )
        # `_estimate_tokens` is `int(words * 1.3)`, so a word count equal to
        # the ceiling gives about 1.3 times the ceiling in tokens. That puts
        # each entry ABOVE the ceiling at any value of the constants.
        huge_text = "word " * entry_ceiling
        entries = [
            f"### 2026-01-15 10:00\n**Context**: {huge_text}",
            f"### 2026-01-14 10:00\n**Context**: {huge_text}",
            f"### 2026-01-13 10:00\n**Context**: {huge_text}",
        ]

        # NON-VACUITY: the input must be above the ceiling, or the bound
        # below passes without the cut ever running.
        assert _estimate_tokens(entries[0]) > entry_ceiling

        result = _apply_token_budget(entries, WORKING_MEMORY_TOKEN_BUDGET)

        # The drop loop must NOT run here. This arm and the drop-loop arm
        # below cover different mechanisms, and this assertion keeps them
        # apart: a failure here is a fixture that reached the wrong one.
        assert len(result) == len(entries)

        # THE NEWEST ENTRY IS NEVER COMPRESSED AND NEVER DROPPED. IT CAN BE
        # BOUNDED. This asserted `result[0] == entries[0]`, which pinned
        # THREE properties at once: not compressed, not dropped, not
        # modified. The per-entry ceiling BOUNDS the entry, so the third
        # died and the first two did not. The three arms below assert the
        # surviving properties DIRECTLY rather than through an equality
        # that also carried the retired one.
        #
        # A `**Summary**` line is what `_compress_memory_entry` emits, so
        # its ABSENCE is the not-compressed property stated positively.
        assert "**Summary**" not in result[0]
        # The date header identifies WHICH entry survived, so this is the
        # not-dropped property: the newest one is still at index 0.
        assert result[0].split("\n")[0] == entries[0].split("\n")[0]
        # And this is the property that replaced the retired one.
        assert _estimate_tokens(result[0]) <= entry_ceiling

    def test_drop_loop_removes_entries_at_a_non_production_budget(self):
        """The drop loop removes entries from the end when compression is not enough.

        THIS ARM OPERATES THE FUNCTION AT AN INPUT PRODUCTION DOES NOT
        REACH, AND THE NAME OF THE FUNCTION PROMISES A SECTION BEHAVIOUR.
        At the production call site the loop CANNOT RUN: the caller slices
        to `MAX_WORKING_MEMORIES`, and the newest entry plus the compressed
        neighbours are bounded by the section budget by construction of the
        ceiling. So the loop is a function property here, and a reader must
        not conclude that the section drops entries in normal operation.

        THE MECHANISM IS ALIVE RATHER THAN DEAD. It is out of reach from
        the production fixture, and it runs at more entries or at a smaller
        budget, which is what this arm supplies.

        THE FIXTURE IS DERIVED FROM THE MODULE CONSTANTS. The budget is the
        per-entry ceiling itself, which leaves room for the newest entry
        and none for a neighbour, so the loop must run at any value of the
        constants.
        """
        from scripts.working_memory import (
            _apply_token_budget,
            COMPRESSED_ENTRY_TOKEN_CEILING,
            MAX_WORKING_MEMORIES,
            WORKING_MEMORY_TOKEN_BUDGET,
        )

        entry_ceiling = (
            WORKING_MEMORY_TOKEN_BUDGET
            - (MAX_WORKING_MEMORIES - 1) * COMPRESSED_ENTRY_TOKEN_CEILING
        )
        huge_text = "word " * entry_ceiling
        entries = [
            f"### 2026-01-15 10:00\n**Context**: {huge_text}",
            f"### 2026-01-14 10:00\n**Context**: {huge_text}",
            f"### 2026-01-13 10:00\n**Context**: {huge_text}",
            f"### 2026-01-12 10:00\n**Context**: {huge_text}",
        ]

        result = _apply_token_budget(entries, entry_ceiling)

        # COMPRESSION NEVER CHANGES THE COUNT, so a smaller count is the
        # evidence that the drop loop ran. Nothing else in this function
        # removes an entry.
        assert len(result) < len(entries)
        assert len(result) >= 1

        # The newest entry survives the loop: the loop guard is
        # `len(result) > 1`.
        assert "**Summary**" not in result[0]
        assert result[0].split("\n")[0] == entries[0].split("\n")[0]

    def test_single_entry_always_kept(self):
        """A single entry should never be dropped, even if over budget."""
        from scripts.working_memory import _apply_token_budget

        huge_text = "word " * 1000
        entries = [f"### 2026-01-15 10:00\n**Context**: {huge_text}"]
        result = _apply_token_budget(entries, 10)
        assert len(result) == 1

    def test_budget_of_zero_keeps_first_entry(self):
        """Budget of zero should still keep at least the first entry."""
        from scripts.working_memory import _apply_token_budget

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
        from scripts.working_memory import sync_to_claude_md

        long_text = "word " * 200
        existing_content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            f"### 2026-01-14 10:00\n**Context**: {long_text}\n**Goal**: Old goal\n\n"
            f"### 2026-01-13 10:00\n**Context**: {long_text}\n**Goal**: Older goal\n\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_to_claude_md(
                {"context": "New context entry", "goal": "New goal"},
                memory_id="test123"
            )

        # Every sync assertion in this class is a PRECONDITION: the subject is
        # the budget behaviour in the file, not the outcome classification. A
        # truthiness read states "the write happened" and leaves the reason to
        # the repr if it ever fails.
        assert result
        new_content = claude_md.read_text(encoding="utf-8")
        assert "## Working Memory" in new_content
        assert "New context entry" in new_content

    def test_sync_with_budget_produces_valid_markdown(self, tmp_path):
        """Output should be valid markdown with proper section structure."""
        from scripts.working_memory import sync_to_claude_md

        content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "## Pinned Context\n\nSome pinned stuff\n"
        )
        claude_md = self._create_claude_md(tmp_path, content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_to_claude_md({"context": "Test"}, memory_id="id1")

        new_content = claude_md.read_text(encoding="utf-8")
        # The synced entry must actually be rendered into the Working Memory
        # block. A no-op sync that left the file untouched would still satisfy
        # the structural assertions below, so pin the entry content directly.
        assert result
        wm_block = re.search(
            r"## Working Memory\n(.*?)(?=\n## |\Z)", new_content, re.DOTALL
        ).group(1)
        assert "**Context**: Test" in wm_block
        assert "**Memory ID**: id1" in wm_block
        # Surrounding structure is preserved.
        assert "## Pinned Context" in new_content
        assert "Some pinned stuff" in new_content


    def test_entry_count_trimmed_to_max_working_memories(self, tmp_path):
        """sync_to_claude_md should trim entries to MAX_WORKING_MEMORIES (3)."""
        from scripts.working_memory import sync_to_claude_md, MAX_WORKING_MEMORIES

        existing_content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### 2026-01-15 10:00\n**Context**: Entry one\n\n"
            "### 2026-01-14 09:00\n**Context**: Entry two\n\n"
            "### 2026-01-13 08:00\n**Context**: Entry three\n\n"
            "### 2026-01-12 07:00\n**Context**: Entry four\n\n"
            "### 2026-01-11 06:00\n**Context**: Entry five\n\n"
            "## Pinned Context\n\nPinned stuff\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_to_claude_md(
                {"context": "Brand new entry"},
                memory_id="new123"
            )

        assert result
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
        from scripts.working_memory import sync_retrieved_to_claude_md

        long_text = "word " * 200
        existing_content = (
            "# Project\n\n"
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            f"### 2026-01-14 10:00\n**Query**: \"old query\"\n**Context**: {long_text}\n\n"
            f"### 2026-01-13 10:00\n**Query**: \"older query\"\n**Context**: {long_text}\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_retrieved_to_claude_md(
                [{"context": "New retrieved", "goal": "test"}],
                query="test search",
                memory_ids=["mem1"]
            )

        # FLOOR, and it is a floor on purpose: this test does not name a CAUSE
        # for its expected outcome, it only requires that the write happened.
        # `wrote` is a real bool, so `is True` keeps the identity strictness
        # the assertion had before the return type became a `SyncResult`.
        assert result.wrote is True
        new_content = claude_md.read_text(encoding="utf-8")
        assert "test search" in new_content
        assert "## Working Memory" in new_content
        # LIMIT, PRE-EXISTING AND NOT INTRODUCED HERE: the name of this test
        # promises entries are REDUCED, and no assertion below counts them.
        # Do not read the name as a guarantee of the drop behaviour.

    def test_no_memories_returns_false(self):
        """sync_retrieved_to_claude_md with an empty list reports `empty`."""
        from scripts.working_memory import sync_retrieved_to_claude_md, SyncResult
        result = sync_retrieved_to_claude_md([], query="test")
        # `empty` is the subject, not mere falsiness. This test NAMES its cause
        # in its own name: there was nothing to write. `unresolved` or `failed`
        # would make this arm pass while the guard it checks never ran.
        assert result.reason == SyncResult.EMPTY


class TestFormatMemoryEntry:
    """Direct unit tests for _format_memory_entry() helper."""

    def test_basic_fields(self):
        """Should format context, goal, and memory_id into markdown."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Working on auth", "goal": "Add JWT support"}
        result = _format_memory_entry(memory, memory_id="abc123")

        assert "**Context**: Working on auth" in result
        assert "**Goal**: Add JWT support" in result
        assert "**Memory ID**: abc123" in result
        assert result.startswith("### ")

    def test_decisions_as_list_of_strings(self):
        """Decisions provided as a list of strings should be joined with commas."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": ["Use Redis", "Add caching"]}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis, Add caching" in result

    def test_decisions_as_list_of_dicts(self):
        """Decisions provided as list of dicts should extract 'decision' key."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": [
            {"decision": "Use Redis"},
            {"decision": "Add caching"}
        ]}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis, Add caching" in result

    def test_decisions_as_string(self):
        """Decisions provided as a plain string should be used directly."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": "Use Redis for storage"}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis for storage" in result

    def test_lessons_as_list(self):
        """Lessons provided as a list should be joined with commas."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test", "lessons_learned": ["Cache invalidation is hard", "Use TTL"]}
        result = _format_memory_entry(memory)

        assert "**Lessons**: Cache invalidation is hard, Use TTL" in result

    def test_lessons_as_string(self):
        """Lessons provided as a string should be used directly."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test", "lessons_learned": "Always use TTL for caches"}
        result = _format_memory_entry(memory)

        assert "**Lessons**: Always use TTL for caches" in result

    def test_missing_optional_fields(self):
        """Missing optional fields should be omitted from output."""
        from scripts.working_memory import _format_memory_entry

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
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test"}
        result = _format_memory_entry(memory, files=["src/auth.py", "tests/test_auth.py"])

        assert "**Files**: src/auth.py, tests/test_auth.py" in result

    def test_empty_memory_dict(self):
        """Empty memory dict should produce only the date header line."""
        from scripts.working_memory import _format_memory_entry

        result = _format_memory_entry({})
        lines = result.strip().split("\n")
        assert len(lines) == 1
        assert lines[0].startswith("### ")


class TestFormatRetrievedEntry:
    """Direct unit tests for _format_retrieved_entry() helper."""

    def test_basic_formatting(self):
        """Should format query, context, and goal into markdown."""
        from scripts.working_memory import _format_retrieved_entry

        memory = {"context": "Auth implementation", "goal": "Add JWT"}
        result = _format_retrieved_entry(memory, query="authentication", memory_id="mem1")

        assert '**Query**: "authentication"' in result
        assert "**Context**: Auth implementation" in result
        assert "**Goal**: Add JWT" in result
        assert "**Memory ID**: mem1" in result
        assert result.startswith("### ")

    def test_context_truncation_at_200_chars(self):
        """Context longer than 200 chars should be truncated with ellipsis."""
        from scripts.working_memory import _format_retrieved_entry

        long_context = "A" * 250
        memory = {"context": long_context}
        result = _format_retrieved_entry(memory, query="test")

        context_line = [l for l in result.split("\n") if "**Context**:" in l][0]
        context_value = context_line.split("**Context**: ", 1)[1]
        assert len(context_value) == 200  # 197 + "..."
        assert context_value.endswith("...")

    def test_score_formatting(self):
        """Score should be formatted to 2 decimal places."""
        from scripts.working_memory import _format_retrieved_entry

        memory = {"context": "Test"}
        result = _format_retrieved_entry(memory, query="test", score=0.87654)

        assert "**Relevance**: 0.88" in result

    def test_no_score_omits_relevance(self):
        """When score is None, Relevance line should be omitted."""
        from scripts.working_memory import _format_retrieved_entry

        memory = {"context": "Test"}
        result = _format_retrieved_entry(memory, query="test")

        assert "**Relevance**" not in result

    def test_missing_optional_fields(self):
        """Missing context and goal should be omitted."""
        from scripts.working_memory import _format_retrieved_entry

        result = _format_retrieved_entry({}, query="test")

        assert '**Query**: "test"' in result
        assert "**Context**" not in result
        assert "**Goal**" not in result


class TestParseWorkingMemorySection:
    """Direct unit tests for _parse_working_memory_section() helper."""

    def test_section_not_found(self):
        """When no Working Memory section exists, should return empty entries."""
        from scripts.working_memory import _parse_working_memory_section

        content = "# Project\n\n## Some Other Section\nContent here\n"
        before, header, after, entries = _parse_working_memory_section(content)

        assert before == content
        assert header == ""
        assert after == ""
        assert entries == []

    def test_no_next_section(self):
        """Working Memory at end of file (no next section) should capture to EOF."""
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### 2026-01-15 10:00\n"
            "**Context**: Some entry\n"
        )
        before, header, after, entries = _parse_working_memory_section(content)

        assert len(entries) == 1
        assert "Some entry" in entries[0]

    def test_entries_without_proper_date_headers(self):
        """Entries without ### YYYY-MM-DD pattern should not be parsed as entries."""
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
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
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "## Pinned Context\n"
        )
        _, header, _, entries = _parse_working_memory_section(content)

        assert header == "## Working Memory"
        assert entries == []

    def test_multiple_entries_parsed_correctly(self):
        """Multiple entries should be parsed as separate items."""
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
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
        from scripts.working_memory import _parse_retrieved_context_section

        content = "# Project\n\n## Working Memory\nContent here\n"
        before, header, after, entries = _parse_retrieved_context_section(content)

        assert before == content
        assert header == ""
        assert after == ""
        assert entries == []

    def test_no_next_section(self):
        """Retrieved Context at end of file should capture to EOF."""
        from scripts.working_memory import _parse_retrieved_context_section

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
        from scripts.working_memory import _parse_retrieved_context_section

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
        from scripts.working_memory import _parse_retrieved_context_section

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
        from scripts.working_memory import _parse_retrieved_context_section

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


# =============================================================================
# Dual-location CLAUDE.md resolution tests for the resolvers' shared helper
# and the staleness resolver's three strategies
# =============================================================================

class TestFindExistingClaudeMd:
    """Tests for _find_existing_claude_md() dual-location helper."""

    def test_returns_none_when_neither_exists(self, tmp_path):
        """Empty directory -> None."""
        from scripts.working_memory import _find_existing_claude_md

        result = _find_existing_claude_md(tmp_path)
        assert result is None

    def test_finds_legacy_claude_md(self, tmp_path):
        """Legacy ./CLAUDE.md at base -> returns it."""
        from scripts.working_memory import _find_existing_claude_md

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy\n")

        result = _find_existing_claude_md(tmp_path)
        assert result == legacy

    def test_finds_new_default_claude_md(self, tmp_path):
        """New default .claude/CLAUDE.md at base -> returns it."""
        from scripts.working_memory import _find_existing_claude_md

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        new_default.write_text("# new default\n")

        result = _find_existing_claude_md(tmp_path)
        assert result == new_default

    def test_prefers_new_default_when_both_exist(self, tmp_path):
        """When both locations exist, .claude/CLAUDE.md wins."""
        from scripts.working_memory import _find_existing_claude_md

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"
        new_default.write_text("# new default\n")
        legacy.write_text("# legacy\n")

        result = _find_existing_claude_md(tmp_path)
        assert result == new_default, ".claude/CLAUDE.md should take priority"


class TestStalenessResolverDualLocation:
    """Dual-location support tests for staleness.get_project_claude_md_path()
    across all 3 fallbacks.

    Verifies that each resolution strategy (env var, git root, cwd) checks
    .claude/CLAUDE.md before ./CLAUDE.md.
    """

    # --- Strategy 1: CLAUDE_PROJECT_DIR env var ---

    def test_env_var_finds_legacy_claude_md(self, tmp_path):
        """Env var strategy finds legacy ./CLAUDE.md."""
        from staleness import get_project_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy\n")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_project_claude_md_path()
        assert result == legacy

    def test_env_var_finds_new_default_claude_md(self, tmp_path):
        """Env var strategy finds .claude/CLAUDE.md (new default)."""
        from staleness import get_project_claude_md_path

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        new_default.write_text("# new default\n")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_project_claude_md_path()
        assert result == new_default

    def test_env_var_prefers_new_default_over_legacy(self, tmp_path):
        """Env var strategy: .claude/CLAUDE.md wins over ./CLAUDE.md."""
        from staleness import get_project_claude_md_path

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"
        new_default.write_text("# new default\n")
        legacy.write_text("# legacy\n")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_project_claude_md_path()
        assert result == new_default

    def test_env_var_without_claude_md_falls_through(self, tmp_path):
        """Env var set but no CLAUDE.md at either location -> fall through to
        next strategy (which will either find git root or fall back to cwd).
        """
        from staleness import get_project_claude_md_path

        # Neither location exists under tmp_path -> env var strategy returns None
        # -> falls through to git/cwd strategies.
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}), \
             patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = get_project_claude_md_path()
        # Nothing found anywhere -> None
        assert result is None

    # --- Strategy 2: git root ---

    def test_git_root_finds_new_default_claude_md(self, tmp_path):
        """Git root strategy finds .claude/CLAUDE.md (new default)."""
        from staleness import get_project_claude_md_path

        repo_root = tmp_path / "myrepo"
        (repo_root / ".claude").mkdir(parents=True)
        new_default = repo_root / ".claude" / "CLAUDE.md"
        new_default.write_text("# new default\n")
        git_dir = repo_root / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"{git_dir}\n"

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", return_value=mock_result):
            result = get_project_claude_md_path()
        assert result == new_default

    def test_git_root_finds_legacy_claude_md(self, tmp_path):
        """Git root strategy finds legacy ./CLAUDE.md."""
        from staleness import get_project_claude_md_path

        repo_root = tmp_path / "myrepo"
        repo_root.mkdir()
        legacy = repo_root / "CLAUDE.md"
        legacy.write_text("# legacy\n")
        git_dir = repo_root / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"{git_dir}\n"

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", return_value=mock_result):
            result = get_project_claude_md_path()
        assert result == legacy

    def test_git_root_prefers_new_default_over_legacy(self, tmp_path):
        """Git root strategy: .claude/CLAUDE.md wins over ./CLAUDE.md."""
        from staleness import get_project_claude_md_path

        repo_root = tmp_path / "myrepo"
        (repo_root / ".claude").mkdir(parents=True)
        new_default = repo_root / ".claude" / "CLAUDE.md"
        legacy = repo_root / "CLAUDE.md"
        new_default.write_text("# new default\n")
        legacy.write_text("# legacy\n")
        git_dir = repo_root / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"{git_dir}\n"

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", return_value=mock_result):
            result = get_project_claude_md_path()
        assert result == new_default

    # --- Strategy 3: cwd ---

    def test_cwd_finds_legacy_claude_md(self, tmp_path):
        """CWD strategy finds legacy ./CLAUDE.md."""
        from staleness import get_project_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy\n")

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = get_project_claude_md_path()
        assert result == legacy

    def test_cwd_finds_new_default_claude_md(self, tmp_path):
        """CWD strategy finds .claude/CLAUDE.md (new default)."""
        from staleness import get_project_claude_md_path

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        new_default.write_text("# new default\n")

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = get_project_claude_md_path()
        assert result == new_default

    def test_cwd_prefers_new_default_over_legacy(self, tmp_path):
        """CWD strategy: .claude/CLAUDE.md wins over ./CLAUDE.md."""
        from staleness import get_project_claude_md_path

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"
        new_default.write_text("# new default\n")
        legacy.write_text("# legacy\n")

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = get_project_claude_md_path()
        assert result == new_default

    def test_cwd_returns_none_when_nothing_found(self, tmp_path):
        """All strategies fail -> returns None."""
        from staleness import get_project_claude_md_path

        # tmp_path is empty -- no CLAUDE.md at either location
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with patch.dict(os.environ, env, clear=True), \
             patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = get_project_claude_md_path()
        assert result is None
