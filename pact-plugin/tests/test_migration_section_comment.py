"""
Location: pact-plugin/tests/test_migration_section_comment.py

Summary: Arms the memory-section comment on the migration path of
`hooks/shared/claude_md_manager.py`. The creation template emitted the two
auto-managed comments and `_build_migrated_content` did not, so a document
carried through migration came out with headings and no comments.

Used by/with:
- hooks/shared/claude_md_manager.py: `RETRIEVED_CONTEXT_COMMENT`,
  `WORKING_MEMORY_COMMENT`, the creation template, and `_build_migrated_content`.
- tests/test_managed_comment_mirror.py: holds the comment TEXT identical across
  the three files that write it. That gate covers TEXT. This file covers
  BEHAVIOUR and the SPELLING COUNT inside one module, which that gate cannot see.

WHY THE ADD IS CONDITIONAL AND WHAT IT COSTS. The emit tests for the comment
ANYWHERE IN THE SECTION BODY rather than at its start. A prefix test gives a
DUPLICATE on three shapes a document really has: the comment after a blank
line, the comment indented, and a different comment first. The wider test
trades that for a suppressed add on a document that quotes the comment deep
inside an entry, and that trade is deliberate: a duplicate cannot be undone by
a later pass, and a missing comment is what the next writer supplies.

WHY IDEMPOTENCY IS ARMED HERE. Migration returns byte-identical on a second
pass, because the marker guard sits above every emit. NOTHING NAMED THAT
PROPERTY IN A TEST BEFORE THIS FILE, which is why an emit change could have
traded it away with nothing going red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

from shared.claude_md_manager import (  # noqa: E402
    MANAGED_START_MARKER,
    RETRIEVED_CONTEXT_COMMENT,
    WORKING_MEMORY_COMMENT,
)
from fixtures.migration_helpers import _build_migrated_content  # noqa: E402

_MANAGER_SOURCE = _PLUGIN_ROOT / "hooks" / "shared" / "claude_md_manager.py"


def _old_format(rc_lead: str = "", wm_lead: str = "") -> str:
    """An old-format document, with each memory section optionally led by text."""
    return (
        "# Project Memory\n\n"
        "## Retrieved Context\n" + rc_lead +
        "\n## Pinned Context\n\n"
        "## Working Memory\n" + wm_lead +
        "### 2026-01-01 10:00\n**Context**: x\n"
    )


class TestTheInstrumentReachesItsSubject:
    """Controls. An arm that migrated nothing would pass vacuously."""

    def test_the_fixture_is_old_format_and_actually_migrates(self):
        source = _old_format()
        assert MANAGED_START_MARKER not in source, (
            "the fixture already carries the managed marker, so the "
            "idempotency guard returns it unchanged and every arm below "
            "measures a no-op"
        )
        out = _build_migrated_content(source)
        assert MANAGED_START_MARKER in out, "migration produced no managed region"

    def test_the_two_comment_constants_are_non_empty(self):
        for name, value in (
            ("RETRIEVED_CONTEXT_COMMENT", RETRIEVED_CONTEXT_COMMENT),
            ("WORKING_MEMORY_COMMENT", WORKING_MEMORY_COMMENT),
        ):
            assert value.strip(), f"{name} resolved to an empty string"


class TestMigrationSuppliesTheMissingComment:
    """The defect: the loop emitted a heading and no comment."""

    def test_a_section_arriving_without_a_comment_gains_one(self):
        out = _build_migrated_content(_old_format())
        assert RETRIEVED_CONTEXT_COMMENT in out, (
            "migration produced `## Retrieved Context` with no auto-managed "
            "comment. A document through this path comes out in a shape the "
            "creation template does not produce."
        )
        assert WORKING_MEMORY_COMMENT in out, (
            "migration produced `## Working Memory` with no auto-managed "
            "comment."
        )

    def test_the_comment_lands_directly_below_its_heading(self):
        out = _build_migrated_content(_old_format())
        assert f"## Retrieved Context\n{RETRIEVED_CONTEXT_COMMENT}" in out, (
            "the retrieved-context comment is present but not on the line "
            "below its heading, which is not the template shape"
        )
        assert f"## Working Memory\n{WORKING_MEMORY_COMMENT}" in out, (
            "the working-memory comment is present but not on the line below "
            "its heading"
        )

    def test_pinned_context_gets_no_comment(self):
        """The creation template gives Pinned Context none, so neither does this."""
        out = _build_migrated_content(_old_format())
        head, _, tail = out.partition("## Pinned Context\n")
        assert tail, "the fixture produced no Pinned Context section"
        first_line = tail.split("\n", 1)[0]
        assert not first_line.strip().startswith("<!--"), (
            f"Pinned Context gained a comment ({first_line!r}). The creation "
            f"template does not give it one, so the two writers now disagree "
            f"in the other direction."
        )


class TestNoSectionEverGainsASecondComment:
    """The failure direction that matters: the population ALREADY correct is
    larger than the population being repaired."""

    @pytest.mark.parametrize(
        "label,lead",
        [
            ("comment at the start", "{c}\n"),
            ("comment after a blank line", "\n{c}\n"),
            ("comment indented", "   {c}\n"),
            ("a different comment first", "<!-- a user note -->\n{c}\n"),
        ],
    )
    def test_a_section_that_carries_the_comment_keeps_exactly_one(self, label, lead):
        source = _old_format(
            lead.format(c=RETRIEVED_CONTEXT_COMMENT),
            lead.format(c=WORKING_MEMORY_COMMENT),
        )
        out = _build_migrated_content(source)
        assert out.count(RETRIEVED_CONTEXT_COMMENT) == 1, (
            f"shape {label!r} produced "
            f"{out.count(RETRIEVED_CONTEXT_COMMENT)} copies of the "
            f"retrieved-context comment. A prefix test rather than a "
            f"containment test is the usual cause."
        )
        assert out.count(WORKING_MEMORY_COMMENT) == 1, (
            f"shape {label!r} produced {out.count(WORKING_MEMORY_COMMENT)} "
            f"copies of the working-memory comment."
        )


class TestAnEarlierReleasesCommentIsReplaced:
    """A section carrying an earlier release's wording of the comment comes
    out with the current comment only. The add-when-absent test reads the old
    wording as absent, so without the drop the two sat side by side."""

    EARLIER = (
        "<!-- Auto-managed by pact-memory skill. Full history searchable via "
        "pact-memory skill. -->",
        "<!-- Auto-managed by pact-memory skill. Full history searchable via "
        "pact-memory skill. Keyed by folder name, so another checkout with the "
        "same name shares this section. -->",
    )

    @pytest.mark.parametrize("earlier", EARLIER)
    @pytest.mark.parametrize("lead", ["{c}\n", "\n{c}\n", "   {c}\n"])
    def test_only_the_current_comment_remains(self, earlier, lead):
        assert earlier != WORKING_MEMORY_COMMENT
        out = _build_migrated_content(_old_format(wm_lead=lead.format(c=earlier)))
        assert earlier not in out
        assert out.count(WORKING_MEMORY_COMMENT) == 1
        assert f"## Working Memory\n{WORKING_MEMORY_COMMENT}\n" in out

    def test_a_quoted_copy_is_the_users_text_and_stays(self):
        """CONTROL: the drop reads marker lines only, so a fenced copy and one
        sharing its line with other text are left alone."""
        earlier = self.EARLIER[1]
        lead = f"```\n{earlier}\n```\nnote: {earlier}\n"
        out = _build_migrated_content(_old_format(wm_lead=lead))
        assert out.count(earlier) == 2
        assert out.count(WORKING_MEMORY_COMMENT) == 1


class TestMigrationStaysIdempotent:
    """NO TEST NAMED THIS BEFORE. The property is real and it is easy to lose
    to an emit change, because losing it reddens nothing else."""

    def test_a_second_pass_returns_byte_identical_content(self):
        once = _build_migrated_content(_old_format())
        twice = _build_migrated_content(once)
        assert twice == once, (
            "migration is no longer idempotent. A second pass changed the "
            "document, so a caller that migrates twice does not converge."
        )

    def test_a_document_already_in_managed_shape_is_returned_unchanged(self):
        once = _build_migrated_content(_old_format())
        assert _build_migrated_content(once) is not None
        assert MANAGED_START_MARKER in once
        assert _build_migrated_content(once) == once


class TestTheCommentIsSpelledInOnePlacePerConstant:
    """LITERAL-ABSENCE GATE, scoped to this module.

    The mirror gate holds this file to the SSOT text. It cannot see HOW MANY
    TIMES the text is spelled here, so a re-introduced literal in the template
    or in the migration loop passes it. This counts.
    """

    @pytest.mark.parametrize(
        "name,value",
        [
            ("RETRIEVED_CONTEXT_COMMENT", RETRIEVED_CONTEXT_COMMENT),
            ("WORKING_MEMORY_COMMENT", WORKING_MEMORY_COMMENT),
        ],
    )
    def test_the_text_appears_once_as_a_source_literal(self, name, value):
        source = _MANAGER_SOURCE.read_text(encoding="utf-8")
        count = source.count(value)
        assert count == 1, (
            f"the {name} text is spelled {count} times in "
            f"{_MANAGER_SOURCE.name}, and it must be spelled once, in the "
            f"constant assignment. A second spelling is a twin inside one "
            f"file, and the two writers here drifted apart that way before."
        )


class TestOnlyTheMemorySectionsOwnCommentsAreDropped:
    """A migration drops a stale section comment only inside a memory
    section, where the rebuilt section writes the current one. A line that
    reads the same in a user's own section is the user's text."""

    USER_COPY = "<!-- Auto-managed by pact-memory skill. A note I keep here. -->"

    def test_a_copy_in_a_user_section_survives(self):
        source = _old_format() + "\n## My Notes\n" + self.USER_COPY + "\nkeep me\n"
        out = _build_migrated_content(source)
        assert self.USER_COPY in out, out
        assert "keep me" in out

    def test_an_old_comment_in_a_memory_section_is_still_replaced(self):
        old = "<!-- Auto-managed by pact-memory skill. Keyed by folder name, an old wording. -->\n"
        out = _build_migrated_content(_old_format(wm_lead=old))
        assert "an old wording" not in out, out
        assert WORKING_MEMORY_COMMENT in out
