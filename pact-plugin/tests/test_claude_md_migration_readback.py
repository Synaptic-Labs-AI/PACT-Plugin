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
one part of the fix); and an input with an uncertain region is refused at the
managed lookup, before any rebuild.
"""

import pytest

import shared.claude_md_manager as claude_md_manager
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
    _plan_migration,
)
from shared.claude_md_markers import State, parse

BOM_BEFORE_FENCE = "﻿```md\n<!-- SESSION_START -->\n```\n## Working Memory\n- entry\n"


def _plan(content):
    new_content, refusal = _plan_migration(content)
    assert refusal is None, refusal
    assert isinstance(new_content, str)
    return new_content


def test_the_byte_order_mark_stays_at_byte_0_and_nowhere_else():
    new_content = _plan(BOM_BEFORE_FENCE)
    assert new_content.startswith("﻿" + MANAGED_START_MARKER)
    assert new_content.count("﻿") == 1
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
