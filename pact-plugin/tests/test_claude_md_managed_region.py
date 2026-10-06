"""
Location: pact-plugin/tests/test_claude_md_managed_region.py
Summary: The managed region, the obsolete kernel block and the migration on
         the fence-aware parser: strip_orphan_kernel_block (and its planner),
         extract_managed_region, migrate_to_managed_structure (and its planner).
Used by: pytest.

Every CLAUDE.md here is written under tmp_path. Rows cover: inline-code and
fenced marker copies left alone; refusals that leave the file byte-identical
and name the line; a <div>-wrapped real block read as absent; an unclosed fence
never answered with an appended block; the non-UTF-8 skip paths planning on the
replace-decoded copy; a memory block with no managed region migrating to one
memory block wherever its session block was; the heading comment added when
only a quote of it is present; and the migration's read-back.
"""

import pytest

from shared import claude_md_manager
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    RETRIEVED_CONTEXT_COMMENT,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    WORKING_MEMORY_COMMENT,
    _plan_migration,
    extract_managed_region,
    migrate_to_managed_structure,
    strip_orphan_kernel_block,
)
from shared.claude_md_markers import Document, Located, State, parse

KS, KE = "<!-- PACT_START: v3.16 -->", "<!-- PACT_END -->"
KERNEL = f"{KS}\nstale persona\n{KE}"
SB = f"{SESSION_START_MARKER}\n## Current Session\n- Resume: `x`\n{SESSION_END_MARKER}"
SECTIONS = (
    "## Retrieved Context\n- r1\n\n## Pinned Context\n### pin1\nbody\n\n"
    "## Working Memory\n### 2026-01-01 a\n")


@pytest.fixture
def home_file(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config / "CLAUDE.md"


@pytest.fixture
def project_file(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project / ".claude" / "CLAUDE.md"


def _str(value):
    """`value`, which the row expects to be text rather than None."""
    assert value is not None
    return value


def _states(text):
    doc = parse(text)
    return (
        doc.find_block(MANAGED_START_MARKER, MANAGED_END_MARKER).state,
        doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER).state,
        doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER).state,
    )


# --- the kernel strip --------------------------------------------------------

@pytest.mark.parametrize("text", [
    f"Docs: `{KS}` opens it and `{KE}` closes it.\n",
    f"Docs: `{KS}` opens it,\nkeep this prose\nand `{KE}` closes it.\n",
])
def test_the_kernel_strip_leaves_prose_between_inline_code_markers(home_file, text):
    home_file.write_text(text, encoding="utf-8")
    assert strip_orphan_kernel_block() is None
    assert home_file.read_text(encoding="utf-8") == text


def test_the_kernel_strip_removes_the_real_block_and_keeps_a_fenced_example(home_file):
    example = f"```\n{KERNEL}\n```\n"
    home_file.write_text(f"# Mine\n{example}\n{KERNEL}\n\nafter\n", encoding="utf-8")
    assert "Removed obsolete PACT kernel block" in _str(strip_orphan_kernel_block())
    assert home_file.read_text(encoding="utf-8") == f"# Mine\n{example}\nafter\n"


@pytest.mark.parametrize("text, line", [
    (f"{KERNEL}\n\n{KERNEL}\n", "lines 1, 5"),  # two blocks
    (f"# pre\n{KS}\n# post\n", "line 2"),  # a start with no end
    (f"intro\n```\nnever closed\n{KERNEL}\n", "line 2"),  # a block past an unclosed fence
    (f"intro\n```\nnever closed\n{KS}\n", "line 2"),  # only its start marker past the fence
    (f"intro\n```\nnever closed\n{KE}\n", "line 2"),  # only its end marker past the fence
])
def test_the_kernel_strip_refuses_a_block_it_cannot_place(home_file, text, line):
    home_file.write_text(text, encoding="utf-8")
    status = _str(strip_orphan_kernel_block())
    assert status.startswith("Migration skipped: ") and line in status
    assert home_file.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("opener, closer", [
    ("<pre>", "a </pre> b"),
    ("<?php", "a ?> b"),
    ("<![CDATA[", "a ]]> b"),
])
def test_the_kernel_strip_refuses_a_removal_that_takes_an_html_blocks_closer(
        home_file, opener, closer):
    # The block holds the line that closes an HTML block opened above it, and a
    # fence follows: without that line the HTML block would cover the fence.
    text = f"# Mine\n{opener}\nnotes\n{KS}\n{closer}\n{KE}\n\n```\nexample\n```\n"
    home_file.write_text(text, encoding="utf-8")
    assert _str(strip_orphan_kernel_block()) == (
        f"Migration skipped: {home_file}: the update would make line 2 start a region "
        "PACT cannot read: an HTML block is never closed. To avoid data loss the file "
        "was left unchanged; inspect it and close the HTML block that line opens.")
    assert home_file.read_text(encoding="utf-8") == text


def test_the_kernel_strip_removes_a_block_after_the_html_blocks_closer(home_file):
    home_file.write_text(f"# Mine\n<pre>\na </pre> b\n{KERNEL}\n\n```\nexample\n```\n",
                         encoding="utf-8")
    assert "Removed obsolete PACT kernel block" in _str(strip_orphan_kernel_block())
    assert home_file.read_text(encoding="utf-8") == "# Mine\n<pre>\na </pre> b\n\n```\nexample\n```\n"


def test_the_kernel_strip_is_silent_on_an_uncertain_region_with_no_kernel_text(home_file):
    # The unclosed fence leaves the rest of the file uncertain, but no kernel
    # marker text is in it, so there is nothing to strip or report. A project
    # file in the same shape is still refused by the migration (the
    # unmigrated-file rows below).
    text = "# Mine\nintro\n```\nnever closed\n"
    home_file.write_text(text, encoding="utf-8")
    assert strip_orphan_kernel_block() is None
    assert home_file.read_text(encoding="utf-8") == text


def test_the_kernel_skip_path_plans_on_the_replace_decoded_copy(home_file, monkeypatch):
    seen = []
    real = claude_md_manager._plan_kernel_strip
    monkeypatch.setattr(claude_md_manager, "_plan_kernel_strip",
                        lambda content, target: seen.append(content) or real(content, target))
    raw = b"caf\xe9\n" + KERNEL.encode() + b"\n"
    home_file.write_bytes(raw)
    assert "not valid UTF-8" in _str(strip_orphan_kernel_block())
    assert seen and seen[-1].startswith("caf�\n")
    assert home_file.read_bytes() == raw


# --- the managed region --------------------------------------------------------

def test_the_managed_region_is_only_a_pair_the_parser_finds():
    region = f"{MANAGED_START_MARKER}\ninside\n{MANAGED_END_MARKER}"
    fenced = f"```\n{region}\n```\n"
    assert extract_managed_region(fenced) is None
    text = fenced + region + "\n"
    found = extract_managed_region(text)
    assert found == ("\ninside\n", len(fenced) + len(MANAGED_START_MARKER))
    assert extract_managed_region(f"see {MANAGED_START_MARKER} here\n{region}\n") is None


# --- the migration: what it refuses and what it leaves alone --------------------

def test_a_div_wrapped_real_block_reads_absent_and_gets_one_fresh_block(project_file):
    # HTML blocks of types 6-7 are not modelled, so the fence inside the <div>
    # makes the real block a fenced example: it is migrated around, once.
    wrapped = (
        f"<div>\n\n```\n{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n{MEMORY_START_MARKER}\n"
        f"## Pinned Context\n{MEMORY_END_MARKER}\n{MANAGED_END_MARKER}\n```\n\n</div>\n")
    project_file.write_text(wrapped, encoding="utf-8")
    assert migrate_to_managed_structure() is not None
    first = project_file.read_text(encoding="utf-8")
    assert _states(first)[:2] == (State.FOUND, State.FOUND)
    assert first.endswith(wrapped)
    assert migrate_to_managed_structure() is None
    assert project_file.read_text(encoding="utf-8") == first


def test_a_migrated_file_with_an_unclosed_fence_below_is_left_alone(project_file):
    text = (
        f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n{MEMORY_START_MARKER}\n"
        f"## Pinned Context\n{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n\nnotes\n```\nopen\n")
    project_file.write_text(text, encoding="utf-8")
    for _ in range(2):
        assert migrate_to_managed_structure() is None
        assert project_file.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text, line", [
    # an unclosed fence before the managed markers
    (f"intro\n```\nopen\n{MANAGED_START_MARKER}\n{MANAGED_END_MARKER}\n", "line 2"),
    # an unclosed fence above a Pinned section: migrating would swallow the
    # pins into the section above, where a later sync trims them
    ("# Project Memory\n\n## Retrieved Context\n- r\n```\nopen\n## Pinned Context\n### p\n", "line 5"),
    # the memory and session blocks are found above the fence, so only the
    # managed pair's own lookup is uncertain
    (f"# Project Memory\n\n{MEMORY_START_MARKER}\n## Retrieved Context\n- r\n{MEMORY_END_MARKER}\n\n"
     f"{SB}\n```\nopen\n## Pinned Context\n### p\n", "line 12"),
])
def test_an_unmigrated_file_with_an_unclosed_fence_is_refused_with_the_notice(
        project_file, text, line):
    project_file.write_text(text, encoding="utf-8")
    for _ in range(2):
        status = _str(migrate_to_managed_structure())
        assert status.startswith("Migration skipped: ")
        assert f"{line} starts an uncertain region" in status
        assert project_file.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text, line", [
    (f"{MANAGED_START_MARKER}\n## Working Memory\n", "line 1"),  # a start with no end
    (f"{SB}\n\n{SB}\n## Working Memory\n", "lines 1, 6"),  # two session blocks
    (f"{MEMORY_START_MARKER}\n## Pinned Context\n", "line 1"),  # a memory start with no end
    (f"see {MEMORY_START_MARKER} here\n## Pinned Context\n", "line 1"),  # a stray memory marker
    ("<!--\n## Pinned Context\n-->\n", "line 2"),  # the only Pinned heading is commented out
])
def test_the_migration_refuses_a_pair_it_cannot_place(project_file, text, line):
    project_file.write_text(text, encoding="utf-8")
    status = _str(migrate_to_managed_structure())
    assert status.startswith("Migration skipped: ") and line in status
    assert project_file.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("title, reason", [
    # a second managed end marker
    (f"# T\n{MANAGED_END_MARKER}", f"{MANAGED_END_MARKER!r} on line 19 has no start marker before it"),
    # a second memory start marker
    (f"# T\n{MEMORY_START_MARKER}",
     f"{MEMORY_START_MARKER!r} on line 5 starts a block inside the block started on line 3"),
    # a session block the file did not have
    (f"# T\n\n{SB}", "it has a Current Session block at line 4 that the original did not have"),
])
def test_a_migration_that_would_not_read_back_is_refused_naming_the_line(monkeypatch, title, reason):
    monkeypatch.setattr(claude_md_manager, "MANAGED_TITLE", title)
    new_content, refusal = _plan_migration("# Project Memory\n\n" + SECTIONS)
    assert new_content is None
    assert refusal == (
        "the migrated file did not read back as one managed block, one memory block "
        f"and the Current Session block it had: {reason}")


def test_a_rebuild_that_loses_the_session_block_is_refused_naming_it_missing(monkeypatch):
    # The rebuilt file loses its Current Session block: its session lookup reads
    # ABSENT, which carries no reason of its own.
    find_block = Document.find_block

    def losing_the_session_block(doc, start, end, scope=None):
        if doc.text.startswith(MANAGED_START_MARKER) and start == SESSION_START_MARKER:
            return Located(State.ABSENT, (), "", None)
        return find_block(doc, start, end, scope)

    monkeypatch.setattr(Document, "find_block", losing_the_session_block)
    new_content, refusal = _plan_migration(f"# Project Memory\n\n{SB}\n" + SECTIONS)
    assert new_content is None
    assert refusal == (
        "the migrated file did not read back as one managed block, one memory block "
        "and the Current Session block it had: its Current Session block is missing")


def test_an_indented_memory_heading_is_not_adopted():
    # The memory headings match at column 0 only; an indented one is a
    # boundary, so its text goes below the managed block, not into Pinned,
    # and keeps its indentation as the user wrote it.
    new_content = _str(_plan_migration("## Retrieved Context\n- r1\n  ## Pinned Context\n### mine\n")[0])
    assert "## Pinned Context\n\n## Working Memory" in new_content
    assert new_content.endswith(f"{MANAGED_END_MARKER}\n\n  ## Pinned Context\n### mine\n")


def test_the_migration_skip_path_plans_on_the_replace_decoded_copy(project_file, monkeypatch):
    seen = []
    real = claude_md_manager._plan_migration
    monkeypatch.setattr(claude_md_manager, "_plan_migration",
                        lambda content: seen.append(content) or real(content))
    raw = b"caf\xe9\n## Working Memory\n"
    project_file.write_bytes(raw)
    assert "not valid UTF-8" in _str(migrate_to_managed_structure())
    assert seen and seen[-1].startswith("caf�\n")
    assert project_file.read_bytes() == raw


def test_the_migration_skip_path_reports_nothing_for_a_migrated_file(project_file):
    project_file.write_bytes(
        f"{MANAGED_START_MARKER}\n# caf".encode() + b"\xe9\n" + f"{MANAGED_END_MARKER}\n".encode())
    assert migrate_to_managed_structure() is None


# --- a memory block with no managed region -------------------------------------

@pytest.mark.parametrize("text, has_session", [
    (f"# Project Memory\n\n{MEMORY_START_MARKER}\n{SB}\n\n{SECTIONS}{MEMORY_END_MARKER}\n", True),
    (f"# Project Memory\n\n{SB}\n\n{MEMORY_START_MARKER}\n{SECTIONS}{MEMORY_END_MARKER}\n", True),
    (f"# Project Memory\n\n{MEMORY_START_MARKER}\n{SECTIONS}{MEMORY_END_MARKER}\n\n{SB}\n", True),
    (f"# Project Memory\n\n{MEMORY_START_MARKER}\n{SECTIONS}{MEMORY_END_MARKER}\n", False),
], ids=["session-inside", "session-before", "session-at-end", "no-session"])
def test_a_memory_block_without_a_managed_region_migrates_to_one_memory_block(
        project_file, text, has_session):
    project_file.write_text(text, encoding="utf-8")
    assert migrate_to_managed_structure() is not None
    out = project_file.read_text(encoding="utf-8")
    session = f"\n{SB}\n" if has_session else ""
    assert out == (
        f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n{session}\n{MEMORY_START_MARKER}\n"
        f"## Retrieved Context\n{RETRIEVED_CONTEXT_COMMENT}\n- r1\n\n"
        "## Pinned Context\n### pin1\nbody\n\n"
        f"## Working Memory\n{WORKING_MEMORY_COMMENT}\n### 2026-01-01 a\n"
        f"{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n")
    assert migrate_to_managed_structure() is None
    assert project_file.read_text(encoding="utf-8") == out


def test_text_between_the_memory_start_row_and_its_first_heading_moves_below(project_file):
    project_file.write_text(
        f"{MEMORY_START_MARKER}\nloose note\n{SECTIONS}{MEMORY_END_MARKER}\n", encoding="utf-8")
    migrate_to_managed_structure()
    out = project_file.read_text(encoding="utf-8")
    assert out.endswith(f"{MANAGED_END_MARKER}\n\nloose note\n")
    assert _states(out) == (State.FOUND, State.FOUND, State.ABSENT)


def test_a_commented_out_section_stays_whole_in_the_section_it_sits_in():
    # A multi-row comment holding old headings is text, not a boundary: the
    # whole comment stays in the Retrieved Context body.
    old = "<!--\n# Old notes\n## Pinned Context\n### stale pin\n-->"
    new_content, _ = _plan_migration(f"## Retrieved Context\n- r1\n{old}\n\n{SECTIONS}")
    assert f"- r1\n{old}\n- r1\n\n## Pinned Context\n### pin1" in _str(new_content)


# --- the heading comment --------------------------------------------------------

@pytest.mark.parametrize("quote", [
    f"```\n{RETRIEVED_CONTEXT_COMMENT}\n```",  # fenced
    f"the file says {RETRIEVED_CONTEXT_COMMENT} here",  # mid-line
    f"`{RETRIEVED_CONTEXT_COMMENT}`",  # inline code
])
def test_a_quote_of_the_heading_comment_is_not_the_comment(quote):
    new_content, _ = _plan_migration(f"## Retrieved Context\n{quote}\n")
    assert f"## Retrieved Context\n{RETRIEVED_CONTEXT_COMMENT}\n{quote}\n" in _str(new_content)
