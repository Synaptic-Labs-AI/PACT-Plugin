"""
Tests for working_memory.py section parsers and the migration->sync pipeline.

Split from test_claude_md_manager.py in #404 round-3 remediation: these tests
specifically exercise the parsers in skills/pact-memory/scripts/working_memory.py
(_parse_working_memory_section, _parse_retrieved_context_section) and the
end-to-end composition of _build_migrated_content with sync_to_claude_md /
sync_retrieved_to_claude_md. Keeping them in their own module keeps
test_claude_md_manager.py focused on shared/claude_md_manager.py itself.

The core concern is that working_memory.py parsers must treat PACT boundary
markers (PACT_MEMORY_*, PACT_MANAGED_*, PACT_ROUTING_*) as section terminators
so a sync round-trip on a new-format file does not silently erode them. The
optional-comment slot in section_pattern must NOT swallow a downstream PACT
marker as the auto-managed comment slot; the next_section_pattern must treat
PACT markers as section terminators.

sys.path is set up by tests/conftest.py (adds hooks/ and skills/pact-memory/
scripts/ to sys.path), so no per-file sys.path manipulation is needed here.
"""

import pytest


# ---------------------------------------------------------------------------
# Marker constants pinned locally so drift in the implementation is caught.
# These mirror the constants in test_claude_md_manager.py; both files carry
# their own copy so either file can be deleted/relocated without coupling.
# ---------------------------------------------------------------------------
_MANAGED_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"


class TestWorkingMemoryParserMarkerPreservation:
    """working_memory.py parsers must treat PACT boundary markers as section
    terminators so sync round-trips don't silently erode PACT_MEMORY_END and
    PACT_MANAGED_END markers (#404 review finding).
    """

    def test_sync_working_memory_preserves_pact_markers(self, tmp_path, monkeypatch):
        """A sync_to_working_memory round-trip on a new-format file must
        preserve PACT_MEMORY_END and PACT_MANAGED_END markers.
        """
        from scripts.working_memory import (
            _parse_working_memory_section,
            WORKING_MEMORY_HEADER,
        )

        new_format_content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            f"{WORKING_MEMORY_HEADER}\n"
            "### 2026-04-12 21:00\n"
            "**Context**: Test entry\n"
            "\n"
            f"{_MEMORY_END}\n"
            "\n"
            f"{_MANAGED_END}\n"
        )

        before, header, after, entries = _parse_working_memory_section(new_format_content)

        # The PACT markers must be in `after`, not consumed as section content
        assert _MEMORY_END in after, (
            "PACT_MEMORY_END marker should be in after_section, not consumed"
        )
        assert _MANAGED_END in after, (
            "PACT_MANAGED_END marker should be in after_section, not consumed"
        )

    def test_sync_retrieved_context_preserves_pact_markers(self, tmp_path, monkeypatch):
        """_parse_retrieved_context_section must also treat PACT markers as
        section terminators.
        """
        from scripts.working_memory import (
            _parse_retrieved_context_section,
            RETRIEVED_CONTEXT_HEADER,
        )

        # Content where Retrieved Context is followed by PACT markers
        # (no Working Memory heading between them)
        content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            f"{RETRIEVED_CONTEXT_HEADER}\n"
            "### 2026-04-12 21:00\n"
            "**Context**: A retrieved memory\n"
            "\n"
            f"{_MEMORY_END}\n"
            "\n"
            f"{_MANAGED_END}\n"
        )

        before, header, after, entries = _parse_retrieved_context_section(content)

        assert _MEMORY_END in after, (
            "PACT_MEMORY_END marker should be in after_section"
        )
        assert _MANAGED_END in after, (
            "PACT_MANAGED_END marker should be in after_section"
        )

    def test_full_round_trip_preserves_markers(self, tmp_path, monkeypatch):
        """A full sync_to_claude_md call must not erode PACT markers."""
        from scripts.working_memory import sync_to_claude_md

        project_dir = tmp_path / "project"
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)
        claude_md = claude_dir / "CLAUDE.md"

        new_format_content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. -->\n"
            "\n"
            f"{_MEMORY_END}\n"
            "\n"
            f"{_MANAGED_END}\n"
        )
        claude_md.write_text(new_format_content, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        memory = {
            "context": "Test context for round-trip",
            "goal": "Verify marker preservation",
            "decisions": ["Decision 1"],
            "lessons_learned": ["Lesson 1"],
        }

        result = sync_to_claude_md(memory, memory_id="test-123")
        assert result is True

        final = claude_md.read_text(encoding="utf-8")
        assert _MEMORY_END in final, (
            "PACT_MEMORY_END must survive sync_to_claude_md round-trip"
        )
        assert _MANAGED_END in final, (
            "PACT_MANAGED_END must survive sync_to_claude_md round-trip"
        )
        assert "Test context for round-trip" in final


class TestMigrationSyncPipeline:
    """End-to-end tests that exercise the exact ACTUAL output of
    _build_migrated_content through sync_to_claude_md and
    sync_retrieved_to_claude_md.

    The existing TestWorkingMemoryParserMarkerPreservation.test_full_round_trip_preserves_markers
    test seeds CLAUDE.md with a pre-existing auto-managed comment on the line
    after ``## Working Memory``. That hides the section_pattern bug: the
    greedy ``(<!-- [^>]*-->)?`` group harmlessly matches the auto-managed
    comment and stops before PACT_MEMORY_END.

    But _build_migrated_content's default empty-memory layout has NO
    auto-managed comment between ``## Working Memory`` and
    ``<!-- PACT_MEMORY_END -->``. Without the (?!PACT_) negative lookahead,
    the optional comment group greedily captures PACT_MEMORY_END, advancing
    section_header_end PAST it. The marker ends up in section_content, gets
    .strip()ed, and is silently dropped on write-back (#404 round-3 review
    finding).

    These tests exercise the bug path directly by using the real output of
    _build_migrated_content as the input to sync.
    """

    def _assert_markers_paired(self, content: str) -> None:
        """All 4 PACT markers must be present AND appear in structural order."""
        assert _MANAGED_START in content, "PACT_MANAGED_START missing"
        assert _MANAGED_END in content, "PACT_MANAGED_END missing"
        assert _MEMORY_START in content, "PACT_MEMORY_START missing"
        assert _MEMORY_END in content, "PACT_MEMORY_END missing"
        # Order: MANAGED_START < MEMORY_START < MEMORY_END < MANAGED_END
        ms = content.index(_MANAGED_START)
        mems = content.index(_MEMORY_START)
        meme = content.index(_MEMORY_END)
        me = content.index(_MANAGED_END)
        assert ms < mems < meme < me, (
            f"Markers out of order: MANAGED_START={ms}, MEMORY_START={mems}, "
            f"MEMORY_END={meme}, MANAGED_END={me}"
        )

    def test_sync_working_memory_against_build_migrated_content_empty_memory(
        self, tmp_path, monkeypatch
    ):
        """sync_to_claude_md on the exact output of _build_migrated_content
        (empty-memory default) must preserve all 4 PACT markers and their
        structural order.

        This is the precise bug path: no auto-managed comment follows
        ``## Working Memory``, so the section_pattern regex's optional
        comment group would greedily swallow ``<!-- PACT_MEMORY_END -->``
        unless the (?!PACT_) negative lookahead blocks it.
        """
        from shared.claude_md_manager import _build_migrated_content
        from scripts.working_memory import sync_to_claude_md

        project_dir = tmp_path / "project"
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)
        claude_md = claude_dir / "CLAUDE.md"

        # Seed with the EXACT output _build_migrated_content produces for
        # a blank "# Project Memory\n" source. This is the empty-memory
        # default layout that triggers the greedy match bug.
        migrated = _build_migrated_content("# Project Memory\n")
        claude_md.write_text(migrated, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        memory = {
            "context": "Migration->sync pipeline test",
            "goal": "Prove section_pattern no longer swallows PACT_MEMORY_END",
            "decisions": ["Use negative lookahead"],
            "lessons_learned": ["Regex ordering matters"],
        }

        result = sync_to_claude_md(memory, memory_id="mig-1")
        assert result is True

        final = claude_md.read_text(encoding="utf-8")
        self._assert_markers_paired(final)
        assert "Migration->sync pipeline test" in final

    def test_sync_retrieved_context_adjacent_to_pact_marker(
        self, tmp_path, monkeypatch
    ):
        """Hand-crafted fixture where ``## Retrieved Context`` is followed
        IMMEDIATELY by ``<!-- PACT_MEMORY_END -->`` with no blank line and no
        other section between them.

        This is the precise shape that triggers the section_pattern bug in
        _parse_retrieved_context_section: the optional comment group
        ``(<!-- [^>]*-->)?`` would greedily consume ``<!-- PACT_MEMORY_END -->``
        as the auto-managed comment slot, advancing section_header_end past
        it. On write-back the marker would be dropped.

        _build_migrated_content's real default layout has all 3 memory
        headings in sequence (Retrieved, Pinned, Working), so the real
        layout does NOT place PACT_MEMORY_END next to Retrieved Context --
        this hand-crafted fixture is required to exercise the parser's
        bug path directly.

        Counter-test: reverting the (?!(?:PACT_MEMORY_|PACT_MANAGED_|
        PACT_ROUTING_)) lookahead in _parse_retrieved_context_section's
        section_pattern causes this test to fail with PACT_MEMORY_END
        absent from the final file.
        """
        from scripts.working_memory import sync_retrieved_to_claude_md

        project_dir = tmp_path / "project"
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)
        claude_md = claude_dir / "CLAUDE.md"

        # Hand-crafted fixture: Retrieved Context is adjacent to PACT_MEMORY_END
        # (no blank line, no other section between).
        fixture = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            f"{_MEMORY_END}\n"
            "\n"
            f"{_MANAGED_END}\n"
        )
        claude_md.write_text(fixture, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        memories = [
            {
                "context": "Retrieved memory content",
                "goal": "Test retrieved sync marker preservation",
                "decisions": ["d1"],
                "lessons_learned": ["l1"],
            }
        ]

        result = sync_retrieved_to_claude_md(
            memories,
            query="test query",
            scores=[0.95],
            memory_ids=["ret-1"],
        )
        assert result is True

        final = claude_md.read_text(encoding="utf-8")
        self._assert_markers_paired(final)
        assert "Retrieved memory content" in final

    def test_full_migration_then_sync_pipeline(self, tmp_path, monkeypatch):
        """Realistic end-to-end: a pre-#404 CLAUDE.md on disk is migrated in
        place by migrate_to_managed_structure, then sync_to_claude_md adds a
        memory entry. All 4 markers must remain present and paired, and the
        new memory entry must appear inside the Working Memory section.

        This is the user-facing path: an existing project upgrading to
        v3.17.0+ will have its CLAUDE.md migrated, and the first subsequent
        sync must not break the managed structure the migration just created.

        The pre-#404 fixture deliberately has an EMPTY Working Memory section
        (just the heading, no entries) so that after migration the parser
        encounters ``## Working Memory`` with a blank body -- the shape that
        triggers the section_pattern's optional-comment-swallow bug. An
        already-populated Working Memory wouldn't drive the bug path because
        the first `### YYYY-MM-DD` line interrupts the optional comment group.

        Counter-test: reverting the (?!(?:PACT_MEMORY_|PACT_MANAGED_|
        PACT_ROUTING_)) lookahead in _parse_working_memory_section's
        section_pattern causes this test to fail with PACT_MEMORY_END
        absent from the final file.
        """
        from shared.claude_md_manager import migrate_to_managed_structure
        from scripts.working_memory import sync_to_claude_md

        project_dir = tmp_path / "project"
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)
        claude_md = claude_dir / "CLAUDE.md"

        # A realistic pre-#404 CLAUDE.md with user content and an EMPTY
        # Working Memory section (no historical entries). This makes the
        # migrated Working Memory section adjacent to PACT_MEMORY_END after
        # _build_migrated_content runs, triggering the parser bug path.
        pre_404_content = (
            "# Project Memory\n"
            "\n"
            "Some user notes that should survive migration.\n"
            "\n"
            "## Working Memory\n"
        )
        claude_md.write_text(pre_404_content, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        # migrate_to_managed_structure resolves the CLAUDE.md location via
        # the same get_project_claude_md_path() helper the hooks use, which
        # honors CLAUDE_PROJECT_DIR.

        migration_msg = migrate_to_managed_structure()
        # Migration should have run (not skipped, not failed)
        assert migration_msg is not None
        assert "failed" not in migration_msg.lower()
        assert "skipped" not in migration_msg.lower()

        post_migration = claude_md.read_text(encoding="utf-8")
        self._assert_markers_paired(post_migration)

        # Sync a new memory after migration
        memory = {
            "context": "End-to-end pipeline verification",
            "goal": "Confirm migration+sync composes correctly",
            "decisions": ["Test the full pipeline"],
            "lessons_learned": ["Greedy regex groups need lookaheads"],
        }

        result = sync_to_claude_md(memory, memory_id="pipeline-1")
        assert result is True

        final = claude_md.read_text(encoding="utf-8")

        # All 4 markers still present and correctly ordered
        self._assert_markers_paired(final)

        # The new memory entry appears inside the Working Memory section
        # (between the Working Memory header and the MEMORY_END marker).
        wm_start = final.index("## Working Memory")
        mem_end = final.index(_MEMORY_END)
        working_memory_region = final[wm_start:mem_end]
        assert "End-to-end pipeline verification" in working_memory_region, (
            "New memory entry should appear inside the Working Memory section"
        )
