"""
Location: pact-plugin/tests/test_claude_md_sync_readable.py

Summary: The Working Memory and Retrieved Context syncs in
skills/pact-memory/scripts/working_memory.py never write a file PACT can read
less of than before, and `uncertainty_added` in
hooks/shared/claude_md_markers.py, the check they share with every writer.

Each sync parses the text it plans to write. It writes nothing, returns
`uncertain` and logs the reason when the plan adds rows the parser cannot
read, or when its section does not read back where it wrote it. The defect
these rows pin: a file ending in a never-closed HTML comment got a section
whose own `<!-- Auto-managed ... -->` row ended the user's comment, so the
file turned uncertain from the comment's line and the new section sat inside
the uncertain region.

SAFETY: every row pins CLAUDE_PROJECT_DIR to tmp_path and passes the declared
anchor, so no resolver can reach a real CLAUDE.md.
"""

from __future__ import annotations

import logging

import pytest

from scripts.working_memory import SyncResult, sync_retrieved_to_claude_md, sync_to_claude_md
from shared.claude_md_markers import Kind, parse, uncertainty_added
from tests import test_pin_marker_writer as _writer_tests

# A Current Session block, then a comment that is never closed and covers no
# fence, so the file reads as certain until a row starting `<!--` ends it.
_OPEN_COMMENT = ("<!-- SESSION_START -->\n- Started: 2020-01-01 00:00:00 UTC\n<!-- SESSION_END -->\n"
                 "<!-- note\nexample\n")

# Its context ends a line with `?>`, the end of a processing instruction.
_MEMORY = {"id": "m1", "context": "the tag ends with ?>", "goal": "a goal",
           "created_at": "2026-01-02T03:04:05+00:00"}

SYNCS = [
    pytest.param("Working Memory", lambda root, path, memory=_MEMORY: sync_to_claude_md(
        memory, target=path, claude_md_root=root), id="working-memory"),
    pytest.param("Retrieved Context", lambda root, path, memory=_MEMORY: sync_retrieved_to_claude_md(
        [memory], "a query", None, ["m1"], claude_md_root=root), id="retrieved-context"),
]


def _project(tmp_path, monkeypatch, doc):
    path = tmp_path / "CLAUDE.md"
    path.write_text(doc, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return path


def _unknown_rows(text):
    return sum(line.kind is Kind.UNKNOWN for line in parse(text).lines)


def _visible_headings(text, name):
    doc = parse(text)
    return [line.row for line in doc.lines
            if line.content.rstrip() == f"## {name}" and line.kind is Kind.PROSE and not line.in_html]


# --------------------------------------------------------------------------
# Refusals leave the file byte-identical and say why


@pytest.mark.parametrize("name, sync", SYNCS)
def test_a_section_that_would_close_an_open_comment_is_not_written(
    tmp_path, monkeypatch, caplog, name, sync
):
    path = _project(tmp_path, monkeypatch, _OPEN_COMMENT)

    with caplog.at_level(logging.WARNING):
        result = sync(tmp_path, path)

    assert result.reason == SyncResult.UNCERTAIN
    assert path.read_text(encoding="utf-8") == _OPEN_COMMENT
    assert ("the update would make line 4 start a region PACT cannot read: "
            "an HTML block is ended only by a line that starts a comment") in caplog.text


@pytest.mark.parametrize("name, sync", SYNCS)
def test_a_section_hidden_by_its_own_entry_is_not_written(tmp_path, monkeypatch, caplog, name, sync):
    """The entry line ending `?>` closes the user's processing instruction at
    a row edge, so the new heading is hidden while no row becomes uncertain:
    only the read-back can see it."""
    doc = "# Notes\n\n<?note\nsome text\n"
    planned_heading_hidden = parse(doc + f"\n## {name}\n<!-- c -->\n\nthe tag ends with ?>\n")
    assert planned_heading_hidden.boundary is None and planned_heading_hidden.lines[5].in_html
    path = _project(tmp_path, monkeypatch, doc)

    with caplog.at_level(logging.WARNING):
        result = sync(tmp_path, path)

    assert result.reason == SyncResult.UNCERTAIN
    assert path.read_text(encoding="utf-8") == doc
    assert f"the {name} section did not read back where it was written" in caplog.text


def test_a_rewrite_that_hides_its_heading_behind_a_later_copy_is_not_written(
    tmp_path, monkeypatch, caplog
):
    """The rewritten section's heading is hidden, so the first visible
    `## Working Memory` is a later copy: the section reads back FOUND, but not
    where the sync wrote it."""
    doc = ("# Notes\n<?note\n## Working Memory\n\n### 2026-01-01 10:00\nold\n\n"
           "## Other\n\n## Working Memory\n\n### 2026-01-02 10:00\na copy\n")
    path = _project(tmp_path, monkeypatch, doc)

    with caplog.at_level(logging.WARNING):
        result = sync_to_claude_md(_MEMORY, target=path, claude_md_root=tmp_path)

    assert result.reason == SyncResult.UNCERTAIN
    assert path.read_text(encoding="utf-8") == doc
    assert "the Working Memory section did not read back where it was written" in caplog.text


# --------------------------------------------------------------------------
# Writes that add nothing uncertain go ahead


@pytest.mark.parametrize("name, sync", SYNCS)
def test_a_closed_comment_is_written_after(tmp_path, monkeypatch, name, sync):
    doc = _OPEN_COMMENT + "-->\n"
    path = _project(tmp_path, monkeypatch, doc)

    assert sync(tmp_path, path)

    written = path.read_text(encoding="utf-8")
    assert written.startswith(doc)
    assert _unknown_rows(written) == 0
    assert len(_visible_headings(written, name)) == 1


@pytest.mark.parametrize("name, sync", SYNCS)
def test_a_section_is_written_above_an_existing_uncertain_region(tmp_path, monkeypatch, name, sync):
    head, tail = _writer_tests.production_head_and_tail()
    doc = head + tail + "\n```\nan unclosed fence below the managed block\n"
    assert _unknown_rows(doc) > 0
    path = _project(tmp_path, monkeypatch, doc)

    assert sync(tmp_path, path)

    written = path.read_text(encoding="utf-8")
    assert _unknown_rows(written) == _unknown_rows(doc)
    assert len(_visible_headings(written, name)) == 1


# --------------------------------------------------------------------------
# uncertainty_added


def test_the_line_named_is_the_line_on_disk_when_rows_are_added_on_both_sides():
    """The stale-pin pass writes a STALE row under a pin above the user's
    never-closed comment and one under a pin below it. The comment's opener
    is line 8 of the file on disk, though row 9 of the planned text."""
    text = ("# Notes\n\n## Pinned Context\n\n### First pin 2020-01-01\nBody one.\n\n"
            "<!-- note\n```\nexample\n\n### Second pin 2020-01-02\nBody two.\n")
    planned = (text.replace("2020-01-01\n", "2020-01-01\n<!-- STALE: Last relevant 2020-01-01 -->\n")
               .replace("2020-01-02\n", "2020-01-02\n<!-- STALE: Last relevant 2020-01-02 -->\n"))
    assert text.split("\n")[7] == "<!-- note"

    assert uncertainty_added(parse(text), parse(planned)) == (
        "the update would make line 8 start a region PACT cannot read: "
        "an HTML block is ended only by a line that starts a comment")


def test_a_line_the_writer_adds_is_named_by_the_line_it_follows():
    assert uncertainty_added(parse("a\nb\n"), parse("a\n```\nb\n")) == (
        "the update would write a line after line 1 that starts a region PACT cannot read: "
        "a code fence is not closed")


def test_a_line_the_writer_adds_at_the_top_is_named_so():
    assert uncertainty_added(parse("a\n"), parse("```\na\n")) == (
        "the update would write a line at the start of the file that starts a region PACT "
        "cannot read: a code fence is not closed")


@pytest.mark.parametrize("before, after", [
    ("a\n", "a\nb\n"),
    ("```\nx\n", "new\n```\nx\n"),  # the boundary moves down, no row is added
    ("```\nx\n", "x\n"),
])
def test_a_plan_that_adds_no_uncertain_row_passes(before, after):
    assert uncertainty_added(parse(before), parse(after)) is None
