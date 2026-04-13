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

    def test_retrieved_context_next_section_stops_at_pact_marker(self):
        r"""Round-4 Item 6: symmetric counter-test for the Retrieved Context
        `next_section_pattern` marker alternative in working_memory.py
        (lines ~524-527).

        Fixture: a Retrieved Context block containing CONTENT (not just
        headers) immediately followed by PACT_MEMORY_END and PACT_MANAGED_END
        with no Pinned Context or Working Memory heading between them. This
        is the precise layout where `next_section_pattern` decides how much
        of the file belongs to the Retrieved Context section.

        Without the `<!-- (?:PACT_MEMORY_|PACT_MANAGED_|PACT_ROUTING_)`
        alternative, `next_section_pattern` scans past the markers looking
        for the next `#`/`##`/`---`, and `section_end` lands at EOF — the
        markers get swallowed into `section_content` and .strip()ed away on
        write-back.

        Counter-test protocol: if the `next_section_pattern` regex at
        working_memory.py:524-527 is reverted to omit the marker alternative
        (e.g., back to `r'^(#\s|##\s(?!Retrieved Context)|---)'`), this test
        must fail because `_MEMORY_END` will not appear in `after_section`.
        """
        from scripts.working_memory import (
            _parse_retrieved_context_section,
            RETRIEVED_CONTEXT_HEADER,
        )

        # Critical fixture shape: Retrieved Context with real content entries
        # immediately followed by PACT markers — no intervening `##` heading.
        # A prior test exercised only the `section_pattern` optional-comment
        # path; this one specifically targets the `next_section_pattern`
        # scan-for-section-end path.
        content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            f"{RETRIEVED_CONTEXT_HEADER}\n"
            "### 2026-04-12 10:00\n"
            "**Query**: \"first query\"\n"
            "**Context**: First retrieved memory body\n"
            "\n"
            "### 2026-04-12 11:00\n"
            "**Query**: \"second query\"\n"
            "**Context**: Second retrieved memory body\n"
            "\n"
            f"{_MEMORY_END}\n"
            "\n"
            f"{_MANAGED_END}\n"
        )

        before, header, after, entries = _parse_retrieved_context_section(content)

        # Primary assertion: PACT markers survive in after_section.
        assert _MEMORY_END in after, (
            "PACT_MEMORY_END must be in after_section — if not, "
            "next_section_pattern scanned past the marker and the sync "
            "round-trip will silently erode the boundary"
        )
        assert _MANAGED_END in after, (
            "PACT_MANAGED_END must be in after_section"
        )

        # Entry extraction must still work — both real entries are captured
        # so the parser isn't just skipping everything.
        assert len(entries) == 2
        assert any("first query" in e for e in entries)
        assert any("second query" in e for e in entries)

        # Neither marker bled into the entries.
        for entry in entries:
            assert "PACT_MEMORY_END" not in entry
            assert "PACT_MANAGED_END" not in entry

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


class TestPACTBoundaryPrefixesTwinDriftDetection:
    """Drift-detection test for the PACT_BOUNDARY_PREFIXES twin (#404 round 5).

    working_memory.py lives under skills/pact-memory/scripts/ and cannot
    cleanly import from hooks/shared/ (separate package). The canonical
    definition lives in hooks/shared/claude_md_manager.py as
    PACT_BOUNDARY_PREFIXES, and working_memory.py maintains a parallel
    _PACT_BOUNDARY_PREFIXES tuple. These tests assert the two tuples stay
    identical so a future prefix addition can't silently leave one parser
    unguarded.
    """

    def test_twin_tuples_are_identical(self):
        """working_memory._PACT_BOUNDARY_PREFIXES must equal
        claude_md_manager.PACT_BOUNDARY_PREFIXES.
        """
        from scripts.working_memory import _PACT_BOUNDARY_PREFIXES
        from shared.claude_md_manager import PACT_BOUNDARY_PREFIXES

        assert _PACT_BOUNDARY_PREFIXES == PACT_BOUNDARY_PREFIXES, (
            "Twin drift detected: working_memory._PACT_BOUNDARY_PREFIXES "
            f"({_PACT_BOUNDARY_PREFIXES!r}) no longer matches "
            f"claude_md_manager.PACT_BOUNDARY_PREFIXES ({PACT_BOUNDARY_PREFIXES!r}). "
            "Update both tuples to keep the twin in sync."
        )

    def test_twin_alternations_are_identical(self):
        """The derived regex-alternation string must also match. This is a
        belt-and-suspenders check — if the tuples agree, the alternations
        built from them must agree too, but this catches an accidental
        divergence in the join expression (e.g. different separator).
        """
        from scripts.working_memory import _PACT_BOUNDARY_ALT
        from shared.claude_md_manager import PACT_BOUNDARY_PREFIXES

        expected_alt = "|".join(PACT_BOUNDARY_PREFIXES)
        assert _PACT_BOUNDARY_ALT == expected_alt, (
            f"Twin alternation mismatch: {_PACT_BOUNDARY_ALT!r} != {expected_alt!r}"
        )


class TestWorkingMemoryParserFenceAware:
    """Round 5 item 4: the sync parsers must track code-fence state so a
    fenced block inside Working Memory or Retrieved Context cannot
    prematurely terminate the section.

    This matters because memory entries sometimes quote code (debugging
    snippets, JSON payloads, config examples). If a fenced block contains
    a line that looks like a section terminator (an H2 heading, ---, or a
    PACT boundary marker), the parser must continue past it and find the
    real terminator.
    """

    def test_fenced_h2_does_not_terminate_working_memory(self):
        """An H2 heading inside a fenced code block within Working Memory
        must not end the section early.
        """
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. -->\n"
            "\n"
            "### 2026-04-12 00:00\n"
            "**Context**: Debugging a parser bug\n"
            "\n"
            "```markdown\n"
            "## This is an example heading\n"
            "shown inside a fence for tutorial purposes\n"
            "```\n"
            "\n"
            "**Decisions**: Use fence-aware line walker\n"
        )

        _before, _header, _after, existing = _parse_working_memory_section(content)
        # Exactly one entry — the fenced H2 did not split it into two or
        # terminate it.
        assert len(existing) == 1
        assert "Debugging a parser bug" in existing[0]
        assert "## This is an example heading" in existing[0]
        assert "Use fence-aware line walker" in existing[0]

    def test_fenced_pact_marker_does_not_terminate_working_memory(self):
        """A PACT boundary marker inside a fenced code block must not be
        treated as a real terminator. The real marker outside the fence
        should terminate the section.
        """
        from scripts.working_memory import _parse_working_memory_section

        content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Working Memory\n"
            "\n"
            "### 2026-04-12 01:00\n"
            "**Context**: Example shown below\n"
            "\n"
            "```\n"
            "<!-- PACT_MEMORY_END -->\n"
            "```\n"
            "\n"
            "**Lessons**: Fenced markers are illustrative\n"
            "\n"
            f"{_MEMORY_END}\n"
            f"{_MANAGED_END}\n"
        )

        _before, _header, after, existing = _parse_working_memory_section(content)

        # The entry survived intact including the fenced marker
        assert len(existing) == 1
        assert "<!-- PACT_MEMORY_END -->" in existing[0]
        assert "Fenced markers are illustrative" in existing[0]

        # The REAL PACT_MEMORY_END and PACT_MANAGED_END markers are in
        # `after_section` — they terminate the Working Memory region.
        assert _MEMORY_END in after
        assert _MANAGED_END in after

    def test_fenced_hr_does_not_terminate_retrieved_context(self):
        """A horizontal rule (`---`) inside a fenced code block must not
        terminate the Retrieved Context section.
        """
        from scripts.working_memory import _parse_retrieved_context_section

        content = (
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. -->\n"
            "\n"
            "### 2026-04-12 02:00\n"
            "**Context**: Example YAML shown below\n"
            "\n"
            "```yaml\n"
            "---\n"
            "key: value\n"
            "```\n"
            "\n"
            "**Lessons**: YAML front-matter uses --- as separator\n"
            "\n"
            "## Pinned Context\n"
        )

        _before, _header, _after, existing = _parse_retrieved_context_section(content)
        assert len(existing) == 1
        # The fenced --- must not have been treated as a terminator
        assert "---" in existing[0]
        assert "key: value" in existing[0]
        assert "YAML front-matter uses" in existing[0]

    def test_unfenced_terminator_still_ends_section(self):
        """Regression guard: the fence-aware refactor must preserve the
        existing terminator behavior for unfenced markers.
        """
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "\n"
            "### 2026-04-12 03:00\n"
            "Simple entry with no fences.\n"
            "\n"
            "## Retrieved Context\n"
            "This must be excluded.\n"
        )

        _before, _header, after, existing = _parse_working_memory_section(content)
        assert len(existing) == 1
        assert "Simple entry with no fences." in existing[0]
        # The downstream section is in `after`, not `existing`
        assert "## Retrieved Context" in after
        for entry in existing:
            assert "This must be excluded" not in entry

    def test_tilde_fenced_pact_marker_does_not_terminate_working_memory(self):
        """Round 8 item 1: `working_memory._find_terminator_offset` must
        recognize tilde fences (CommonMark §4.5) as well as backtick
        fences. Pre-round-8, the walker only tracked ``` fences, so a
        user-authored ~~~ fence containing a fake PACT boundary marker
        would cause the terminator search to stop inside the fence,
        losing the tail of the Working Memory section.

        This test is the counterpart to
        `test_fenced_pact_marker_does_not_terminate_working_memory` —
        same adversarial content, wrapped in ~~~ instead of ```.
        """
        from scripts.working_memory import _parse_working_memory_section

        content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Working Memory\n"
            "\n"
            "### 2026-04-12 04:00\n"
            "**Context**: Tilde-fenced example below\n"
            "\n"
            "~~~\n"
            "<!-- PACT_MEMORY_END -->\n"
            "~~~\n"
            "\n"
            "**Lessons**: Tilde fences are equivalent to backtick fences\n"
            "\n"
            f"{_MEMORY_END}\n"
            f"{_MANAGED_END}\n"
        )

        _before, _header, after, existing = _parse_working_memory_section(content)

        # The entry survives intact INCLUDING the tilde-fenced fake marker
        # and the post-fence lessons line.
        assert len(existing) == 1
        assert "<!-- PACT_MEMORY_END -->" in existing[0]
        assert (
            "Tilde fences are equivalent to backtick fences" in existing[0]
        )
        # The REAL PACT_MEMORY_END marker is in `after_section`
        assert _MEMORY_END in after
        assert _MANAGED_END in after
