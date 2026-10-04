"""
Location: pact-plugin/tests/test_claude_md_sync_sections.py

Summary: The Working Memory and Retrieved Context syncs in
skills/pact-memory/scripts/working_memory.py locate their sections through the
fence-aware parser (hooks/shared/claude_md_markers.py), and a missing section
is written INSIDE the block that bounds the write window.

The defect these rows pin: a missing section used to be appended at the end of
the file, outside the memory block, while the next sync searched only inside
the block, so every sync appended another copy. A fenced example of the
heading is an example: it is never the section, and its bytes come out
unchanged. A heading the parser can only see inside an HTML comment makes the
sync refuse and leave the file byte-identical. A PACT marker line ends a
section, so a rewrite never takes the marker or what follows it.

The real-heading count uses its own naive fence toggle, not the parser under
test, so a parser that read a fenced heading as real cannot also pass the
count.

SAFETY: every row pins CLAUDE_PROJECT_DIR to tmp_path and passes the declared
anchor, so no resolver can reach a real CLAUDE.md.
"""

from __future__ import annotations

import pytest

from scripts import working_memory
from scripts.working_memory import (
    MAX_RETRIEVED_MEMORIES,
    MAX_WORKING_MEMORIES,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    RETRIEVED_CONTEXT_HEADER,
    WORKING_MEMORY_HEADER,
    SyncResult,
    _parse_working_memory_section,
    project_memories_to_claude_md,
    sync_retrieved_to_claude_md,
    sync_to_claude_md,
)
from shared.claude_md_manager import MANAGED_END_MARKER
from tests import test_pin_marker_writer as _writer_tests

production_head_and_tail = _writer_tests.production_head_and_tail

MEMORY = {"id": "m1", "context": "a context", "goal": "a goal",
          "created_at": "2026-01-02T03:04:05+00:00"}
PINS = "## Pinned Context\n\n### A pin\nSome pinned prose.\n\n"


def _sync_working(root, path):
    return sync_to_claude_md(MEMORY, target=path, claude_md_root=root)


def _sync_retrieved(root, path):
    return sync_retrieved_to_claude_md([MEMORY], "a query", None, ["m1"], claude_md_root=root)


WRITERS = [
    pytest.param(WORKING_MEMORY_HEADER, _sync_working, id="working-memory"),
    pytest.param(RETRIEVED_CONTEXT_HEADER, _sync_retrieved, id="retrieved-context"),
]


def _fenced(heading: str) -> str:
    return f"```markdown\n{heading}\n\n### 2026-01-01 example\n```\n"


def _project(tmp_path, monkeypatch, doc: str):
    root = tmp_path / "project"
    root.mkdir()
    path = root / "CLAUDE.md"
    path.write_text(doc, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root, path


def _real_rows(text: str, heading: str) -> list[int]:
    """Rows that are `heading` and sit outside a ``` fence (naive toggle)."""
    rows, fenced = [], False
    for row, line in enumerate(text.split("\n")):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.rstrip() == heading:
            rows.append(row)
    return rows


def _row_of(text: str, needle: str) -> int:
    return text[:text.index(needle)].count("\n")


def _assert_one_section_in_the_memory_block(text: str, heading: str) -> None:
    rows = _real_rows(text, heading)
    assert len(rows) == 1, f"{len(rows)} real {heading!r} sections:\n{text}"
    assert _row_of(text, MEMORY_START_MARKER) < rows[0] < _row_of(text, MEMORY_END_MARKER)


def _memory_block(body: str) -> str:
    head, tail = production_head_and_tail()
    return head + body + tail


# --------------------------------------------------------------------------
# A missing section goes inside the memory block, once


@pytest.mark.parametrize("heading, sync", WRITERS)
@pytest.mark.parametrize("fenced_example", [False, True], ids=["no-heading", "only-fenced"])
def test_a_missing_section_is_written_inside_the_memory_block(
    tmp_path, monkeypatch, heading, sync, fenced_example
):
    body = PINS + (_fenced(heading) if fenced_example else "")
    doc = _memory_block(body)
    root, path = _project(tmp_path, monkeypatch, doc)

    result = sync(root, path)

    assert result, result
    written = path.read_text(encoding="utf-8")
    _assert_one_section_in_the_memory_block(written, heading)
    # Inserted at the start of the memory block's closing marker row: every
    # byte before it and from it on is unchanged.
    split = doc.index(MEMORY_END_MARKER)
    assert written.startswith(doc[:split])
    assert written.endswith(doc[split:])
    if fenced_example:
        assert written.count(_fenced(heading)) == 1


@pytest.mark.parametrize("heading, sync", WRITERS)
@pytest.mark.parametrize("fenced_example", [False, True], ids=["no-heading", "only-fenced"])
def test_a_second_sync_adds_no_second_section(tmp_path, monkeypatch, heading, sync, fenced_example):
    body = PINS + (_fenced(heading) if fenced_example else "")
    root, path = _project(tmp_path, monkeypatch, _memory_block(body))

    assert sync(root, path)
    assert sync(root, path)

    _assert_one_section_in_the_memory_block(path.read_text(encoding="utf-8"), heading)


@pytest.mark.parametrize("fenced_example", [False, True], ids=["no-heading", "only-fenced"])
def test_a_repeated_working_memory_projection_is_byte_identical(tmp_path, monkeypatch, fenced_example):
    """The first projection inserts the section; the second finds it and
    rebuilds it from the same records. The two shapes must agree byte for
    byte, or every sync rewrites the file."""
    body = PINS + (_fenced(WORKING_MEMORY_HEADER) if fenced_example else "")
    root, path = _project(tmp_path, monkeypatch, _memory_block(body))
    memories = [MEMORY, dict(MEMORY, id="m2", context="another", created_at="2026-01-01T00:00:00+00:00")]

    assert project_memories_to_claude_md(memories, target=path, claude_md_root=root)
    first = path.read_bytes()
    assert project_memories_to_claude_md(memories, target=path, claude_md_root=root)

    assert path.read_bytes() == first
    _assert_one_section_in_the_memory_block(first.decode("utf-8"), WORKING_MEMORY_HEADER)


@pytest.mark.parametrize("fenced_example", [False, True], ids=["no-heading", "only-fenced"])
def test_a_full_retrieved_context_section_is_a_fixed_point(tmp_path, monkeypatch, fenced_example):
    """The Retrieved Context sync always prepends the new top result, so the
    file stops changing only once the section is full. With a constant entry,
    one more sync after that leaves the file byte-identical."""
    body = PINS + (_fenced(RETRIEVED_CONTEXT_HEADER) if fenced_example else "")
    root, path = _project(tmp_path, monkeypatch, _memory_block(body))
    monkeypatch.setattr(working_memory, "_format_retrieved_entry",
                        lambda *args: "### 2026-01-02 03:04\n**Query**: \"q\"\n**Context**: c")

    for _ in range(MAX_RETRIEVED_MEMORIES):
        assert _sync_retrieved(root, path)
    full = path.read_bytes()
    assert _sync_retrieved(root, path)

    assert path.read_bytes() == full
    _assert_one_section_in_the_memory_block(full.decode("utf-8"), RETRIEVED_CONTEXT_HEADER)


# --------------------------------------------------------------------------
# The other windows


@pytest.mark.parametrize("heading, sync", WRITERS)
def test_with_no_memory_pair_the_section_goes_above_the_managed_end(tmp_path, monkeypatch, heading, sync):
    """With the memory markers gone, the window is the session-block end to
    the managed end, and a missing section goes inside the managed block."""
    head, tail = production_head_and_tail()
    doc = head.replace(MEMORY_START_MARKER + "\n", "") + PINS + tail.replace(MEMORY_END_MARKER + "\n", "")
    assert MEMORY_START_MARKER not in doc and MEMORY_END_MARKER not in doc
    root, path = _project(tmp_path, monkeypatch, doc)

    assert sync(root, path)

    written = path.read_text(encoding="utf-8")
    split = doc.index(MANAGED_END_MARKER)
    assert written.startswith(doc[:split]) and written.endswith(doc[split:])
    assert len(_real_rows(written, heading)) == 1


@pytest.mark.parametrize("heading, sync", WRITERS)
def test_a_pact_marker_line_ends_the_section(tmp_path, monkeypatch, heading, sync):
    """With no managed block the window is the whole file, so the memory end
    marker below the section is inside it. That marker ends the section, so
    the marker and the prose under it survive the rewrite.

    The section is full, so the sync drops its last entry. Were the marker
    read as part of that entry, it would be dropped with it."""
    tail = f"{MEMORY_END_MARKER}\n\nProse below the memory block.\n"
    full = max(MAX_WORKING_MEMORIES, MAX_RETRIEVED_MEMORIES)
    entries = "".join(f"### 2026-01-{day:02d} 10:00\nold\n\n" for day in range(full, 0, -1))
    doc = f"# My notes\n\n{MEMORY_START_MARKER}\n{heading}\n\n{entries}{tail}"
    root, path = _project(tmp_path, monkeypatch, doc)

    assert sync(root, path)

    written = path.read_text(encoding="utf-8")
    assert written.endswith(tail) and written.count(MEMORY_END_MARKER) == 1
    assert len(_real_rows(written, heading)) == 1


@pytest.mark.parametrize("heading, sync", WRITERS)
def test_a_file_with_no_managed_block_still_appends_at_the_end(tmp_path, monkeypatch, heading, sync):
    doc = "# My notes\n\nSome prose.\n"
    root, path = _project(tmp_path, monkeypatch, doc)

    assert sync(root, path)

    assert path.read_text(encoding="utf-8").startswith(doc + "\n" + heading + "\n")


# --------------------------------------------------------------------------
# Refusals leave the file byte-identical


@pytest.mark.parametrize("heading, sync", WRITERS)
def test_no_managed_block_and_an_unclosed_fence_refuses(tmp_path, monkeypatch, heading, sync):
    doc = "# My notes\n\n```\nan open fence that never closes\n"
    root, path = _project(tmp_path, monkeypatch, doc)

    result = sync(root, path)

    assert not result
    assert result.reason == SyncResult.NO_WINDOW
    assert path.read_text(encoding="utf-8") == doc


@pytest.mark.parametrize("heading, sync", WRITERS)
def test_a_heading_seen_only_inside_a_comment_refuses(tmp_path, monkeypatch, heading, sync):
    doc = _memory_block(PINS + f"<!--\n{heading}\nan old section\n-->\n")
    root, path = _project(tmp_path, monkeypatch, doc)

    result = sync(root, path)

    assert result.reason == SyncResult.UNCERTAIN
    assert path.read_text(encoding="utf-8") == doc


# --------------------------------------------------------------------------
# Entries


def test_a_fenced_date_line_does_not_split_an_entry():
    entry = "### 2026-01-02 10:00\n**Context**: shows an example\n```\n### 2026-01-01 not an entry\n```"
    doc = _memory_block(f"{WORKING_MEMORY_HEADER}\n\n{entry}\n")

    parsed = _parse_working_memory_section(doc)

    assert isinstance(parsed, tuple)
    assert parsed[3] == [entry]
