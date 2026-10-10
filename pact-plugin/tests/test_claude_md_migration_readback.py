"""
Location: pact-plugin/tests/test_claude_md_migration_readback.py
Summary: The migration into the managed structure keeps the user's bytes, and
         refuses a rebuilt file that does not read back the way it must.
Used by: pytest.

The corpus file's migration arm checks every corpus file's user lines byte for
byte. These rows pin what that arm cannot name one by one: the byte-order mark
stays at byte 0 and nowhere else; the first kept line keeps its indentation
and the last keeps its trailing spaces; each read-back condition refuses a
rebuild that changed the user's text (driven through test doubles that undo
one part of the fix); an input whose marker already reads stray is refused
when a rebuild makes a further copy live, including the real rebuild moving an
open HTML block above a fenced copy; a reorder that changes how a carried line
reads is refused; the legacy loader line is dropped only where the original
file reads it as prose; and an input with an uncertain region is refused at
the managed lookup, before any rebuild.
"""

import pytest

import shared.claude_md_manager as claude_md_manager
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    _drop_spans,
    _legacy_line_spans,
    _legacy_spans_after_cuts,
    _marker_span,
    _plan_migration,
)
from shared.claude_md_markers import Cause, State, parse

BOM_BEFORE_FENCE = "\ufeff```md\n<!-- SESSION_START -->\n```\n## Working Memory\n- entry\n"


def _plan(content):
    new_content, refusal = _plan_migration(content)
    assert refusal is None, refusal
    assert isinstance(new_content, str)
    return new_content


def test_the_byte_order_mark_stays_at_byte_0_and_nowhere_else():
    new_content = _plan(BOM_BEFORE_FENCE)
    assert new_content.startswith("\ufeff" + MANAGED_START_MARKER)
    assert new_content.count("\ufeff") == 1
    assert new_content.count("```md\n") == 1 and "\n```\n" in new_content
    assert _plan_migration(new_content) == (None, None)


def test_the_first_kept_line_keeps_its_indentation_and_the_last_its_trailing_spaces():
    content = "\n\n    ```\n    indented, not a fence\nlast line  \n\n\n## Working Memory\n- entry\n"
    new_content = _plan(content)
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\n    ```\n    indented, not a fence\nlast line  \n")
    assert parse(new_content).boundary is None


@pytest.fixture
def strip_the_user_text(monkeypatch):
    """Undo the edge fix: strip the user text, the first line's indentation with it."""
    monkeypatch.setattr(claude_md_manager, "_trim_blank_edges", str.strip)


def test_a_rebuild_that_leaves_an_uncertain_region_is_refused(strip_the_user_text):
    # Every other read-back condition holds here: the session block was
    # extracted and every carried marker sits in the memory block, above the
    # fence the strip opens. Only the uncertain-region condition can refuse it.
    content = (
        "    ```\n"
        "## Pinned Context\n"
        f"{PINNED_START_MARKER}\n<!-- pinned: 2026-04-20 -->\n### A pin\nbody\n{PINNED_END_MARKER}\n"
        "## Working Memory\n"
        "<!-- PACT_ROUTING_START: legacy -->\n<!-- PACT_ROUTING_END -->\n"
        "<!-- PACT_START: v1 -->\n<!-- PACT_END -->\n"
        "<!-- SESSION_START -->\nResume here\n<!-- SESSION_END -->\n"
    )
    assert parse(content).boundary is None
    new_content, refusal = _plan_migration(content)
    assert new_content is None
    assert refusal is not None and "region PACT cannot read" in refusal


@pytest.mark.parametrize("line, reason", [
    ("<!-- PACT_ROUTING_START: legacy -->", "the migrated file would change how the routing block reads"),
    (PINNED_START_MARKER, f"the migrated file would change how {PINNED_START_MARKER!r} reads"),
], ids=["routing block", "pinned marker"])
def test_a_rebuild_that_makes_a_carried_marker_live_is_refused(strip_the_user_text, line, reason):
    # Indented four spaces, the line is not a marker; stripped to column 0 it
    # would be one. No region becomes uncertain and no managed, memory or
    # session block changes.
    new_content, refusal = _plan_migration(f"    {line}\n## Working Memory\n- entry\n")
    assert new_content is None
    assert refusal == reason


@pytest.fixture
def drop_the_fence_lines(monkeypatch):
    """A rebuild that loses the user text's fence lines, so a fenced example goes live."""
    monkeypatch.setattr(
        claude_md_manager, "_trim_blank_edges",
        lambda text: "\n".join(line for line in text.split("\n") if not line.startswith("```")).strip(),
    )


def test_a_rebuild_that_unfences_a_second_routing_pair_is_refused(drop_the_fence_lines):
    # A real routing pair and a fenced example of one. A rebuild that loses the
    # fence lines makes the example live: two pairs where there was one. Read
    # as a block the routing lookup goes from FOUND to DUPLICATE; read by its
    # shared prefix it is DUPLICATE both times and the damage goes unseen.
    content = (
        "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
        "<!-- PACT_ROUTING_END -->\n"
        "```md\n<!-- PACT_ROUTING_START: example -->\n<!-- PACT_ROUTING_END -->\n```\n"
        "## Working Memory\n- entry\n"
    )
    new_content, refusal = _plan_migration(content)
    assert new_content is None
    assert refusal == "the migrated file would change how the routing block reads"


def test_a_rebuild_that_unfences_a_third_copy_of_a_duplicate_marker_is_refused(drop_the_fence_lines):
    # The marker already reads DUPLICATE, so its state and cause are the same
    # after the fenced copy goes live. Only the number of marker lines changes.
    content = (
        f"{PINNED_START_MARKER}\nnotes\n{PINNED_START_MARKER}\n"
        f"```md\n{PINNED_START_MARKER}\n```\n"
        "## Working Memory\n- entry\n"
    )
    assert parse(content).find_marker(PINNED_START_MARKER).state is State.DUPLICATE
    new_content, refusal = _plan_migration(content)
    assert new_content is None
    assert refusal == f"the migrated file would change how {PINNED_START_MARKER!r} reads"


def test_a_rebuild_that_unfences_a_second_unpaired_routing_end_is_refused(drop_the_fence_lines):
    # A routing end with no start reads MALFORMED, unpaired, as a block, with
    # or without a second copy, so the block lookup cannot see the copy go
    # live. The end marker read on its own goes from FOUND to DUPLICATE.
    content = (
        "<!-- PACT_ROUTING_END -->\n"
        "```md\n<!-- PACT_ROUTING_END -->\n```\n"
        "## Working Memory\n- entry\n"
    )
    new_content, refusal = _plan_migration(content)
    assert new_content is None
    assert refusal == "the migrated file would change how '<!-- PACT_ROUTING_END -->' reads"


def test_an_honest_rebuild_of_a_duplicate_marker_migrates_unchanged():
    # Every marker line is carried as written, so a marker that already reads
    # DUPLICATE reads DUPLICATE on as many lines after the rebuild.
    content = f"{PINNED_START_MARKER}\nnotes\n{PINNED_START_MARKER}\n## Working Memory\n- entry\n"
    new_content = _plan(content)
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\n{PINNED_START_MARKER}\nnotes\n{PINNED_START_MARKER}\n")
    assert len(parse(new_content).find_marker(PINNED_START_MARKER).spans) == 2


def test_a_live_unpaired_routing_marker_in_the_users_notes_migrates_unchanged():
    # The honest rebuild moves the user's notes below the managed block, so every
    # line number in a lookup's reason shifts. The routing lookup reads the same
    # state and cause before and after, so the file is written, line for line.
    content = "Notes\n<!-- PACT_ROUTING_START: left over -->\nmore notes\n## Working Memory\n- entry\n"
    routing = ("<!-- PACT_ROUTING_START", "<!-- PACT_ROUTING_END -->")
    before = parse(content).find_block(*routing)
    new_content = _plan(content)
    after = parse(new_content).find_block(*routing)
    assert (after.state, after.cause) == (before.state, before.cause)
    assert after.reason != before.reason
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes\n<!-- PACT_ROUTING_START: left over -->\nmore notes\n")


def test_a_rebuild_that_moves_the_byte_order_mark_is_refused(monkeypatch):
    # Undo the byte-order-mark fix: the mark stays on the user's first line,
    # which then sits mid-file, where the fence it starts is no longer one.
    monkeypatch.setattr(claude_md_manager, "_split_bom", lambda content: ("", content))
    new_content, refusal = _plan_migration(BOM_BEFORE_FENCE)
    assert new_content is None
    assert refusal is not None


def test_an_uncertain_input_is_refused_at_the_managed_lookup_before_any_rebuild():
    # A found session block above an unclosed fence in the user's own text: the
    # managed lookup reads UNKNOWN, so the rebuild is never reached.
    content = (
        "<!-- SESSION_START -->\nResume here\n<!-- SESSION_END -->\n"
        "## Working Memory\n- entry\n# Notes\n```\nnever closed\n"
    )
    managed = parse(content).find_block(MANAGED_START_MARKER, MANAGED_END_MARKER)
    assert managed.reason
    assert _plan_migration(content) == (None, managed.reason)


# An input whose marker already reads stray reads stray after any rebuild that
# keeps that stray, so state and cause cannot see a further copy going live.
# The number of clean marker lines a stray result carries can.

STRAY_ROUTING_AND_FENCED_EXAMPLE = (
    "> <!-- PACT_ROUTING_START: quoted -->\n> <!-- PACT_ROUTING_END -->\n"
    "```md\n<!-- PACT_ROUTING_START: example -->\n<!-- PACT_ROUTING_END -->\n```\n"
    "## Working Memory\n- entry\n"
)
TWO_STRAY_PINNED_STARTS = f"    {PINNED_START_MARKER}\n> {PINNED_START_MARKER}\n## Working Memory\n- entry\n"
STRAY_PINNED_AND_REAL_PINNED_SECTION = (
    f"> {PINNED_START_MARKER}\n"
    "## Pinned Context\n"
    f"{PINNED_START_MARKER}\n<!-- pinned: 2026-04-20 -->\n### A pin\nbody\n{PINNED_END_MARKER}\n"
    "## Working Memory\n- entry\n"
)
ROUTING = ("<!-- PACT_ROUTING_START", "<!-- PACT_ROUTING_END -->")


def _reads_stray_with_no_marker_line(content, *literals):
    """The literal (or the block of a start and end literal) reads stray in
    `content`, and no line of it is a marker line."""
    doc = parse(content)
    located = doc.find_block(*literals) if len(literals) == 2 else doc.find_marker(*literals)
    return ((located.state, located.cause) == (State.MALFORMED, Cause.STRAY)
            and not any(doc.marker_rows(literal) for literal in literals))


def test_a_rebuild_that_makes_a_fenced_copy_live_in_a_stray_input_is_refused(drop_the_fence_lines):
    # Both quoted routing markers are stray before and after, so every routing
    # lookup reads stray both times; the example's two lines become live marker
    # lines, and only their count changes.
    assert _reads_stray_with_no_marker_line(STRAY_ROUTING_AND_FENCED_EXAMPLE, *ROUTING)
    new_content, refusal = _plan_migration(STRAY_ROUTING_AND_FENCED_EXAMPLE)
    assert new_content is None
    assert refusal == "the migrated file would change how the routing block reads"


def test_a_rebuild_that_turns_one_of_two_strays_into_a_marker_line_is_refused(strip_the_user_text):
    # Stripping the user text de-indents the first copy into a marker line; the
    # quoted copy keeps the literal stray.
    assert _reads_stray_with_no_marker_line(TWO_STRAY_PINNED_STARTS, PINNED_START_MARKER)
    new_content, refusal = _plan_migration(TWO_STRAY_PINNED_STARTS)
    assert new_content is None
    assert refusal == f"the migrated file would change how {PINNED_START_MARKER!r} reads"


@pytest.mark.parametrize("content", [
    STRAY_ROUTING_AND_FENCED_EXAMPLE, TWO_STRAY_PINNED_STARTS, STRAY_PINNED_AND_REAL_PINNED_SECTION,
], ids=["fenced routing example", "two stray pinned starts", "stray pinned and a real Pinned section"])
def test_an_input_that_reads_stray_migrates_under_the_real_rebuild(content):
    _plan(content)


def test_a_rebuild_that_moves_an_open_html_block_above_a_fenced_marker_is_refused():
    # The Pinned section ends inside an HTML block that never closes. Moved into
    # the memory block above the user's notes, it covers their fence, so the
    # rebuilt file reads uncertain and the fenced kernel marker is never
    # written out as a live line.
    content = (
        "Notes\n~~~\n<!-- PACT_START: v2 -->\n~~~\nsee <!-- PACT_START: v3 --> here\n"
        "## Pinned Context\n### p\n<![CDATA[\n## Working Memory\n- e\n"
    )
    assert _reads_stray_with_no_marker_line(content, "<!-- PACT_START:")
    assert _plan_migration(content) == (None, CDATA_OVER_A_FENCE_REFUSAL)


# A Pinned section that ends inside a CDATA block that is never closed takes the
# text the rebuild moves below it into that block.
UNCLOSED_CDATA_PIN = "## Pinned Context\n### A pin\n<![CDATA[\nraw\n"


# Moved above the user's text, the never-closed CDATA block covers a fence opener,
# so the rebuilt file reads uncertain from that block on and the managed lookup
# cannot find its end.
CDATA_OVER_A_FENCE_REFUSAL = (
    "the migrated file did not read back as one managed block, one memory block and the Current "
    "Session block it had: '<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this "
    "block -->' on line 1 has no end marker before the uncertain region; line 10 starts an "
    "uncertain region: an HTML block is never closed")


def test_a_reorder_that_swallows_the_users_fenced_code_is_refused():
    content = f"Notes\n```sh\necho hi\n```\n{UNCLOSED_CDATA_PIN}"
    assert _plan_migration(content) == (None, CDATA_OVER_A_FENCE_REFUSAL)


def test_the_same_file_with_the_block_closed_migrates_with_its_fence():
    new_content = _plan(f"Notes\n```sh\necho hi\n```\n{UNCLOSED_CDATA_PIN}]]>\n")
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes\n```sh\necho hi\n```\n")


def test_a_reorder_that_swallows_a_memory_sections_fenced_code_is_refused():
    content = f"## Working Memory\n- entry\n```sh\necho hi\n```\n{UNCLOSED_CDATA_PIN}"
    assert _plan_migration(content) == (None, CDATA_OVER_A_FENCE_REFUSAL)


def test_a_reorder_that_closes_an_html_block_over_carried_rows_is_refused():
    # Every row stays prose: only whether an HTML block hides it changes. The
    # user's `]]>` closes the block the moved Pinned section opens.
    content = f"Notes\nmore\n]]>\n{UNCLOSED_CDATA_PIN}"
    assert _plan_migration(content) == (None, "the migrated file would change how line 10 reads")


def test_a_session_block_that_opens_an_html_block_over_a_fence_is_refused():
    # The session block's `<?php` is never closed and covers the `~~~` below it,
    # which would open a fence if the `<?php` were prose: the original reads
    # uncertain from line 5 and is not migrated.
    content = (
        "## Working Memory\n- entry\n"
        "<!-- SESSION_START -->\n## Current Session\n<?php\n<!-- SESSION_END -->\n"
        "Notes\n~~~\n"
    )
    assert _plan_migration(content) == (
        None, "line 5 starts an uncertain region: an HTML block is never closed")


# Two documents from the fence oracle's generator (seed 1, documents 1224 and
# 1353). In each, a session block opens an HTML block that is never closed and
# covers rows that would start a structure if its opener were prose (a comment
# start, a fence), so the original reads uncertain from that opener and is not
# migrated.
SESSION_BLOCK_COVERS_THE_REST = (
    "see the ## Working Memory section\r\n<!-- SESSION_START -->\n<?php\r<!-- SESSION_END -->\n"
    "    `````a`b\n---\r\n<!-- PACT_START: x\n<!-- PACT_START: x\n    four spaces\n<!-- PACT_END -->\n"
    "## Working Memory\r\nentry\n## Working Memory\ntext <!-- mid -->\r\n## Working Memory\n- e\n"
)
FENCE_TURNS_TO_PROSE_BELOW_A_CUT = (
    "\ufeff~~~ md\r\n## Pinned Context\n~~~\r<!-- PACT_START: v3 -->\ruse `<!-- SESSION_START -->` here\r"
    "<!-- PACT_END -->\n\n<!-- SESSION_START -->\r<script type=x>\n<!-- SESSION_END -->\n```\u2028\r<prefix\r\n"
    "<?php\n## Pinned Context\n?>\n## Pinned Context  "
)


def test_a_generated_file_whose_session_block_covers_the_rest_is_refused():
    assert _plan_migration(SESSION_BLOCK_COVERS_THE_REST) == (
        None, "line 3 starts an uncertain region: an HTML block is never closed")


def test_a_generated_file_whose_fence_turns_to_prose_below_a_cut_is_refused():
    assert _plan_migration(FENCE_TURNS_TO_PROSE_BELOW_A_CUT) == (
        None, "line 9 starts an uncertain region: an HTML block is never closed")


# The stale loader line from the legacy template is dropped only where the
# user's file reads it as visible prose; a quote of it in the user's code, or
# inside an HTML block that hides it, stays.

LOADER = "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`."


@pytest.mark.parametrize("block", [
    "<!--\nold template, kept for reference:\n" + LOADER + "\n-->\n",
    "<!-- note\n" + LOADER + "\n-->\n",
    "<!-- " + LOADER + " -->\n",
    "<pre>\n" + LOADER + "\n</pre>\n",
    "<script>\n" + LOADER + "\n</script>\n",
    "<?php\n" + LOADER + "\n?>\n",
    "<![CDATA[\n" + LOADER + "\n]]>\n",
], ids=["comment on its own rows", "comment under its opener row", "comment on one row", "pre",
        "script", "processing instruction", "CDATA"])
def test_a_loader_line_inside_an_html_block_that_hides_it_is_kept(block):
    # The top-level copy goes; the copy the block hides is the user's text.
    new_content = _plan("# Project Memory\n\n" + LOADER + "\n\nNotes\n" + block + "## Working Memory\n- entry\n")
    assert new_content.count(LOADER) == 1
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes\n{block}")


@pytest.mark.parametrize("block", [
    "<!DOCTYPE note\n" + LOADER + "\n>\n",
    "<details>\n<summary>old</summary>\n\n" + LOADER + "\n</details>\n",
    "<div>\n" + LOADER + "\n</div>\n",
], ids=["declaration", "details", "div"])
def test_a_loader_line_under_a_block_the_parser_reads_as_visible_is_dropped(block):
    # The parser hides no declaration and does not model blocks like <details>
    # or <div>: their rows are visible prose to every reader, so the line goes.
    new_content = _plan("# Project Memory\n\nNotes\n" + block + "## Working Memory\n- entry\n")
    assert LOADER not in new_content
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes\n" + block.replace(LOADER + "\n", ""))


@pytest.mark.parametrize("notes", [
    "```text\n    ```\n" + LOADER + "\n```\n",  # an indented fence-like line does not close the fence
    "<!--\n```\n-->\n```\n" + LOADER + "\n```\n",  # a fence-like line inside a comment opens nothing
], ids=["indented closer inside a fence", "fence line inside a comment"])
def test_a_loader_line_quoted_in_the_users_code_is_kept(notes):
    new_content = _plan("# Project Memory\n\n" + notes + "## Working Memory\n- entry\n")
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\n{notes}")


def test_a_loader_line_in_the_users_prose_is_dropped():
    new_content = _plan("# Project Memory\n\nNotes\n" + LOADER + "\nmore\n## Working Memory\n- entry\n")
    assert LOADER not in new_content
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes\nmore\n")


def test_a_loader_line_below_a_cut_session_block_is_dropped_in_place():
    session = f"{SESSION_START_MARKER}\n## Current Session\n- Resume: `x`\n{SESSION_END_MARKER}"
    new_content = _plan(f"# Project Memory\n\n{session}\nNotes\n{LOADER}\nmore\n## Working Memory\n- entry\n")
    assert LOADER not in new_content and session in new_content
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes\nmore\n")


def test_a_loader_line_inside_the_session_block_stays_in_it():
    # A row the session cut covers is never dropped, and the text after the
    # cut is not shifted by a drop meant for a row inside it.
    session = f"{SESSION_START_MARKER}\n## Current Session\n{LOADER}\n{SESSION_END_MARKER}"
    new_content = _plan(f"# Project Memory\n\n{session}\nNotes after\n## Working Memory\n- entry\n")
    assert session in new_content
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\nNotes after\n")


def test_a_loader_line_the_original_reads_as_code_is_not_dropped(monkeypatch):
    # The session block closes an HTML block opened above it, and a stray
    # `</pre>` sits inside the fence, above the quote. With the session block
    # cut out, the `<pre>` closes at that stray line instead, hiding the fence
    # opener, so the text left reads the quote as visible prose; the original
    # reads it as code, and the drop is decided on the original.
    content = (
        f"# Project Memory\n\n<pre>\n{SESSION_START_MARKER}\n## Current Session\n</pre>\n"
        f"{SESSION_END_MARKER}\n```text\n</pre>\n{LOADER}\n```\n## Working Memory\n- entry\n"
    )
    doc = parse(content)
    cuts = [(_marker_span(doc, 3)[0], _marker_span(doc, 6)[1])]
    assert _legacy_line_spans(parse(_drop_spans(content, cuts)))  # the cut text would drop it
    assert _legacy_spans_after_cuts(doc, cuts, 0) == []
    # The migration decides the drop on the original file. The rebuild then
    # moves the session block away from the HTML block it closed, so the
    # `<pre>` hides the fence opener and the closing fence line opens a fence
    # that never closes. It refuses: the quote is never written out without
    # its line.
    seen = []
    monkeypatch.setattr(claude_md_manager, "_legacy_spans_after_cuts",
                        lambda doc, *rest: seen.append(doc.text) or _legacy_spans_after_cuts(doc, *rest))
    new_content, refusal = _plan_migration(content)
    assert seen == [content]
    assert new_content is None and refusal is not None
    assert "would leave a region PACT cannot read" in refusal
