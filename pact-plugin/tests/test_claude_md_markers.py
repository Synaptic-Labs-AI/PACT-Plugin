"""
Location: pact-plugin/tests/test_claude_md_markers.py
Summary: Unit rows for the fence-aware CLAUDE.md parser in
         hooks/shared/claude_md_markers.py: every corpus file against its
         hand-written expected states, then line splitting, fences, HTML
         blocks and in_html rows, inline code spans, container fences,
         prefix markers, state precedence, empty scopes, find_section and
         the locating API.
Used by: pytest.

The corpus and its expected table live in tests/fixtures/claude_md_corpus/ and
are written by hand, never generated from the parser. The marker constants
come from their shipped module, not from the parser.
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared import claude_md_markers
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
)
from shared.claude_md_markers import Cause, Kind, State, parse

_CORPUS = Path(__file__).parent / "fixtures" / "claude_md_corpus"
_EXPECTED = json.loads((_CORPUS / "expected.json").read_text(encoding="utf-8"))
# The legacy kernel markers are locals of claude_md_manager._plan_kernel_strip,
# so they cannot be imported; the start is a prefix literal.
KERNEL_START, KERNEL_END = "<!-- PACT_START:", "<!-- PACT_END -->"
_PAIRS = {
    "SESSION": (SESSION_START_MARKER, SESSION_END_MARKER),
    "MEMORY": (MEMORY_START_MARKER, MEMORY_END_MARKER),
    "MANAGED": (MANAGED_START_MARKER, MANAGED_END_MARKER),
    "PINNED": (PINNED_START_MARKER, PINNED_END_MARKER),
    "KERNEL": (KERNEL_START, KERNEL_END),
}
S, E = SESSION_START_MARKER, SESSION_END_MARKER
# The section-ending marker prefixes of the heading recipe (architecture R3).
BOUNDARY_PREFIXES = (
    "<!-- PACT_MEMORY_", "<!-- PACT_MANAGED_", "<!-- PACT_ROUTING_", "<!-- SESSION_",
)
PINNED_HEADING = re.compile(r"## Pinned Context\s*$")
PINNED_TERMINATOR = re.compile(r"#{1,2}\s")


def _doc(*lines, sep="\n"):
    return parse(sep.join(lines) + sep)


def _kinds(doc):
    """Run-length kinds, as the corpus table writes them: 'P3 F1 C2'."""
    runs = []
    for line in doc.lines:
        letter = line.kind.value[0]
        if runs and runs[-1][0] == letter:
            runs[-1][1] += 1
        else:
            runs.append([letter, 1])
    return " ".join(f"{letter}{count}" for letter, count in runs)


def _session(doc, scope=None):
    return doc.find_block(S, E, scope)


def _scope(value):
    return None if value is None else tuple(value)


def _located(located):
    """A Located as the corpus table writes it."""
    return (located.state.value, [list(span) for span in located.spans],
            located.cause and located.cause.value)


def _want(entry):
    return entry["state"], entry.get("spans", []), entry.get("cause")


def _in_html_rows(doc):
    return [line.row for line in doc.lines if line.in_html]


# --- the corpus ------------------------------------------------------------

def test_corpus_is_populated():
    assert len(_EXPECTED) >= 40
    assert {p.stem for p in _CORPUS.glob("*.md")} == set(_EXPECTED)


@pytest.mark.parametrize("case", sorted(_EXPECTED))
def test_corpus_expected_states(case):
    expected = _EXPECTED[case]
    errors = "replace" if expected.get("decode") == "replace" else "strict"
    doc = parse((_CORPUS / f"{case}.md").read_bytes().decode("utf-8", errors=errors))
    assert _kinds(doc) == expected["kinds"]
    assert doc.boundary == expected["boundary"]
    assert (doc.boundary_cause and doc.boundary_cause.value) == expected["boundary_cause"]
    for name, want in expected["blocks"].items():
        assert _located(doc.find_block(*_PAIRS[name])) == _want(want), name
    for pattern, rows in expected.get("headings", {}).items():
        assert list(doc.find_lines(re.compile(pattern))) == rows, pattern
    # A file with no html_rows key has no in_html rows.
    assert _in_html_rows(doc) == expected.get("html_rows", [])
    for want in expected.get("markers", []):
        located = doc.find_marker(want["literal"], _scope(want["scope"]))
        assert _located(located) == _want(want), want
    for want in expected.get("sections", []):
        terminator = want["terminator"] and re.compile(want["terminator"])
        located = doc.find_section(re.compile(want["heading"]), terminator, _scope(want["scope"]),
                                   stop_prefixes=tuple(want["stop_prefixes"]),
                                   unique=want["unique"])
        assert _located(located) == _want(want), want


# --- lines, BOM, line endings ----------------------------------------------

@pytest.mark.parametrize("text, contents", [
    ("", []),
    ("a", ["a"]),
    ("a\n", ["a"]),
    ("\n", [""]),
    ("a\r\nb", ["a", "b"]),
    ("a\rb\n", ["a", "b"]),
    ("a\u2028b\x0cc\u0085d\n", ["a\u2028b\x0cc\u0085d"]),
])
def test_rows_end_only_at_cr_lf_and_crlf(text, contents):
    doc = parse(text)
    assert [line.content for line in doc.lines] == contents
    assert "".join(text[line.start:line.end] for line in doc.lines) == text


@pytest.mark.parametrize("text, row, start", [
    ("\ufeffa\nb\n", 0, 1),  # after the byte-order mark
    ("a\nb\n", 0, 0),
    ("\ufeffa\r\nbc\n", 1, 4),  # a later row starts where its line does
])
def test_row_start_is_where_the_content_begins(text, row, start):
    doc = parse(text)
    assert doc.row_start(row) == start
    assert text[start:start + len(doc.lines[row].content)] == doc.lines[row].content


def test_bom_is_outside_row_zero_content_and_inside_offsets():
    text = "\ufeff" + S + "\nbody\n" + E + "\n"
    doc = parse(text)
    assert doc.lines[0].content == S
    assert doc.lines[0].start == 0
    assert _session(doc).spans == ((0, 2),)
    start, end = doc.offsets(0, 2)
    assert text[start:end] == text


def test_crlf_fenced_example_is_ignored_and_crlf_block_is_found():
    fenced = _doc("```", S, E, "```", sep="\r\n")
    assert _session(fenced).state is State.ABSENT
    real = _doc("# notes", S, "x", E, sep="\r\n")
    assert _session(real).spans == ((1, 3),)


# --- fences ----------------------------------------------------------------

def test_four_backtick_fence_holds_a_triple_backtick_line():
    doc = _doc("````", "```", S, E, "```", "````", "after")
    assert _kinds(doc) == "F1 C4 F1 P1"
    assert _session(doc).state is State.ABSENT


def test_tilde_fence_is_not_closed_by_backticks():
    doc = _doc("prose", "~~~", S, E, "```", "tail")
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.UNCLOSED_FENCE)
    assert _session(doc).state is State.UNKNOWN


def test_backtick_info_string_with_a_backtick_opens_nothing():
    assert _kinds(_doc("```a`b", S, E)) == "P3"
    assert _kinds(_doc("~~~a`b", "x", "~~~")) == "F1 C1 F1"


def test_closer_followed_by_text_does_not_close():
    doc = _doc("```", "``` not a closer", "```")
    assert _kinds(doc) == "F1 C1 F1"


def test_fence_indent_three_spaces_opens_and_four_does_not():
    assert _kinds(_doc("   ```", "x", "   ```")) == "F1 C1 F1"
    assert _kinds(_doc("    ```", "x", "    ```")) == "P3"


def test_two_space_fence_in_a_list_item_is_code():
    doc = _doc("- Example:", "", "  ```", "  " + S, "  " + E, "  ```", "after")
    assert _kinds(doc) == "P2 F1 C2 F1 P1"
    assert _session(doc).state is State.ABSENT


# --- the marker-line rule and stray markers --------------------------------

@pytest.mark.parametrize("prefix, suffix, state", [
    ("", "", State.FOUND),
    ("   ", " \t ", State.FOUND),
    ("    ", "", State.MALFORMED),
    ("\t", "", State.MALFORMED),
    ("> ", "", State.MALFORMED),
    ("- ", "", State.MALFORMED),
    ("", " trailing words", State.MALFORMED),
])
def test_marker_line_rule(prefix, suffix, state):
    doc = _doc(prefix + S + suffix, "x", E)
    located = _session(doc)
    assert located.state is state
    if state is State.MALFORMED:
        assert located.cause is Cause.STRAY
        assert "line 1" in located.reason


def test_indented_code_example_of_the_block_is_stray():
    doc = _doc("notes", "", "    " + S, "    x", "    " + E)
    located = _session(doc)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)
    assert "lines 3, 5" in located.reason


def test_inline_code_mention_alone_is_absent():
    doc = _doc(f"Markers look like `{S}` and `{E}` in prose.")
    assert _session(doc).state is State.ABSENT


def test_inline_code_mention_plus_a_real_block_is_found():
    doc = _doc(f"See ``{S}`` below.", S, "x", E)
    assert _session(doc).spans == ((1, 3),)


@pytest.mark.parametrize("line", [
    f"Unquoted {S} mid-line.",
    f"Unmatched `{S} backtick.",
    f"Unequal ``{S}` runs.",
])
def test_marker_text_outside_an_inline_span_is_stray(line):
    located = _session(_doc(line, S, "x", E))
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


@pytest.mark.parametrize("line, state", [
    ("see \\`" + S + "\\` here", State.MALFORMED),  # escaped backticks open no span
    ("see \\\\`" + S + "` here", State.ABSENT),  # an escaped backslash: the run opens
    ("see \\``" + S + "` here", State.ABSENT),  # the escaped run opens one backtick shorter
    ("see `" + S + "\\` here", State.ABSENT),  # a closer is matched raw
    ("see \\`" + S + "` here", State.MALFORMED),  # an escaped single backtick opens nothing
])
def test_backslash_escapes_on_inline_code_runs(line, state):
    assert _session(_doc(line)).state is state


# A stray result's spans are the clean marker lines, the lines that take effect
# once the strays are fixed; its reason names the stray lines.
@pytest.mark.parametrize("lines, spans, stray_line", [
    (("> " + S,), (), "line 1"),
    (("> " + S, "x", S), ((2, 2),), "line 1"),
    ((S, "x", "> " + S, S), ((0, 0), (3, 3)), "line 3"),
])
def test_a_stray_marker_carries_its_clean_marker_lines_as_spans(lines, spans, stray_line):
    located = _doc(*lines).find_marker(S)
    assert (located.state, located.spans, located.cause) == (State.MALFORMED, spans, Cause.STRAY)
    assert f"marker text on {stray_line} " in located.reason


def test_a_stray_block_carries_its_clean_start_and_end_lines_as_spans():
    located = _session(_doc("> " + S, S, "x", E))
    assert (located.state, located.spans, located.cause) == (
        State.MALFORMED, ((1, 1), (3, 3)), Cause.STRAY)
    assert "marker text on line 1 " in located.reason


def test_a_clean_marker_line_outside_the_scope_is_not_a_span():
    located = _doc(S, "x", "> " + S, S).find_marker(S, (1, 3))
    assert (located.state, located.spans, located.cause) == (State.MALFORMED, ((3, 3),), Cause.STRAY)


# --- prefix markers --------------------------------------------------------

@pytest.mark.parametrize("start_line", [
    KERNEL_START + " v3 -->", KERNEL_START + "v3.16 -->", "   " + KERNEL_START + " v3 --> \t",
])
def test_prefix_marker_line_is_the_literal_and_the_rest_of_one_comment(start_line):
    doc = _doc("intro", start_line, "kernel text", KERNEL_END)
    assert doc.find_block(KERNEL_START, KERNEL_END).spans == ((1, 3),)


def test_prefix_inside_an_inline_code_span_is_a_mention():
    doc = _doc(f"The old kernel began `{KERNEL_START} v3 -->`.")
    assert doc.find_block(KERNEL_START, KERNEL_END).state is State.ABSENT


@pytest.mark.parametrize("bad", [
    "see " + KERNEL_START + " v3 -->",  # mid-line
    KERNEL_START + " v3 --> trailing words",  # words after the comment
    KERNEL_START + " a --> <!-- b -->",  # two comments
    "    " + KERNEL_START + " v3 -->",  # indented code
])
def test_prefix_not_on_a_marker_line_is_stray(bad):
    doc = _doc(bad, KERNEL_START + " v3 -->", "x", KERNEL_END)
    located = doc.find_block(KERNEL_START, KERNEL_END)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


def test_prefix_whose_comment_does_not_close_on_its_line_is_stray():
    located = _doc(KERNEL_START + " v3").find_block(KERNEL_START, KERNEL_END)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


def test_prefix_matches_every_marker_it_begins():
    doc = _doc(MEMORY_START_MARKER, PINNED_START_MARKER, PINNED_END_MARKER, MEMORY_END_MARKER)
    assert doc.marker_rows("<!-- PACT_MEMORY_") == (0, 1, 2, 3)
    assert doc.marker_rows(MEMORY_START_MARKER) == (0,)


# --- HTML blocks -----------------------------------------------------------

def test_backticks_inside_a_comment_open_no_fence():
    doc = _doc("<!-- sample:", "```", "-->", S, "x", E)
    assert _kinds(doc) == "P6"
    assert _session(doc).spans == ((3, 5),)


def test_comment_quoting_an_opener_mid_line_is_fine():
    doc = _doc("<!-- note", "a quoted <!-- mid-line -->", S, "x", E)
    assert doc.boundary is None
    assert _session(doc).spans == ((2, 4),)


@pytest.mark.parametrize("indent", ["", "   "])
def test_comment_ended_by_a_line_start_marker_is_a_boundary(indent):
    doc = _doc("intro", indent + "<!-- TODO", indent + S, "x", E)
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.COMMENT_BOUNDARY)
    located = _session(doc)
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.COMMENT_BOUNDARY)
    assert "line 2" in located.reason


def test_four_space_comment_start_opens_no_html_block():
    doc = _doc("    <!-- TODO", "```", "x", "```")
    assert _kinds(doc) == "P1 F1 C1 F1"
    assert doc.boundary is None


@pytest.mark.parametrize("start", ["<!-->", "<!--->", "<?>", "<!DOCTYPE html>", "<pre>x</pre>"])
def test_html_block_ending_on_its_start_line_suppresses_nothing_after(start):
    doc = _doc(start, "```", S, E, "```")
    assert _kinds(doc) == "P1 F1 C2 F1"
    assert _session(doc).state is State.ABSENT


@pytest.mark.parametrize("start, end", [
    ("<pre>", "</PRE>"), ("<?php", "?>"), ("<!DOCTYPE", ">"), ("<![CDATA[", "]]>"),
])
def test_open_html_block_suppresses_fences_until_its_end(start, end):
    doc = _doc(start, "```", end, S, "x", E)
    assert _kinds(doc) == "P6"
    assert _session(doc).spans == ((3, 5),)


def test_types_six_and_seven_are_not_modelled():
    doc = _doc("<div>", "```", S, E, "```", "</div>")
    assert _kinds(doc) == "P1 F1 C2 F1 P1"
    assert _session(doc).state is State.ABSENT
    assert _in_html_rows(_doc("<div>", "## Working Memory", "</div>")) == []


@pytest.mark.parametrize("start, ender", [
    ("<!DOCTYPE html", S),
    ("<!ELEMENT note", S),
    ("<pre>", KERNEL_START + " </pre> -->"),
    ("<?php", KERNEL_START + " ?> -->"),
    ("<![CDATA[", KERNEL_START + " ]]> -->"),
])
def test_any_html_block_ended_by_a_line_start_comment_is_a_boundary(start, ender):
    doc = _doc("intro", start, ender, "x", E)
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.COMMENT_BOUNDARY)
    located = _session(doc)
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.COMMENT_BOUNDARY)
    assert "an HTML block is ended only by a line that starts a comment" in located.reason


def test_declaration_closed_by_a_prose_line_sets_no_boundary():
    doc = _doc("<!DOCTYPE html", "a line holding > in prose", S, "x", E)
    assert doc.boundary is None
    assert _session(doc).spans == ((2, 4),)


# --- in_html rows ----------------------------------------------------------

_WM_HEADING = re.compile(r"## Working Memory\s*$")


def test_hidden_rows_are_prose_read_by_find_lines_and_skipped_as_headings():
    doc = _doc("<!-- old:", "## Working Memory", "-->", "## Working Memory")
    assert _in_html_rows(doc) == [0, 1, 2]
    assert [line.kind for line in doc.lines] == [Kind.PROSE] * 4
    assert doc.find_lines(_WM_HEADING) == (1, 3)
    assert doc.find_section(_WM_HEADING, None).spans == ((3, 3),)


@pytest.mark.parametrize("lines, rows", [
    (("<!--", "## old", "-->"), [0, 1, 2]),  # the closer alone
    (("<!--", "## old", "  -->  "), [0, 1, 2]),  # spaces around it
    (("<!--", "## old", "--> kept for reference"), [0, 1, 2]),  # the row begins with it
    (("<!-- old:", "## old", "end of the old section -->"), [0, 1, 2]),  # the row ends with it
    (("<pre>", "## old", "x</PRE>"), [0, 1, 2]),
    (("<?php", "## old", "?>"), [0, 1, 2]),
    (("<![CDATA[", "## old", "]]>"), [0, 1, 2]),
])
def test_block_closed_at_a_row_edge_hides_its_rows(lines, rows):
    assert _in_html_rows(_doc("intro", *lines)) == [row + 1 for row in rows]


@pytest.mark.parametrize("lines", [
    ("<!-- pinned: 2026-01-01 -->", "<?x?>", "<pre>x</pre>"),  # one-row blocks
    ("intro", "<pre>", "## Working Memory"),  # never closed before end of file
    ("<!-- TODO tidy", "## Working Memory", "flow: a --> b"),  # closed mid-line
    ("<pre>", "## Working Memory", "a </pre> b"),
    ("<!DOCTYPE html", "## Working Memory", ">"),  # a declaration never hides
    ("<!Note to self", "## Working Memory", "see -> here"),
])
def test_blocks_that_hide_nothing(lines):
    doc = _doc(*lines)
    assert _in_html_rows(doc) == []
    if any(_WM_HEADING.match(line) for line in lines):
        assert doc.find_section(_WM_HEADING, None).state is State.FOUND


def test_one_row_comment_stays_findable():
    doc = _doc("### pin", "<!-- pinned: 2026-01-01 -->")
    assert doc.find_lines(re.compile(r"<!-- pinned:")) == (1,)


def test_unknown_rows_are_never_in_html():
    doc = _doc("<!-- TODO", S, "x", E)
    assert all(line.kind is Kind.UNKNOWN and not line.in_html for line in doc.lines)


def test_marker_lookups_still_read_in_html_rows():
    doc = _doc("<pre>", S, "x", E, "</pre>")
    assert _in_html_rows(doc) == [0, 1, 2, 3, 4]
    assert _session(doc).spans == ((1, 3),)


# --- container fences ------------------------------------------------------

@pytest.mark.parametrize("opener", [
    "- ```bash", "* ```", "+ ~~~", "1. ```", "2) ```", "> ```", ">```", "> - ```",
    "- \t```bash", "-     ```", "> \t```", "-\t```", ">> ~~~~",
])
def test_fence_on_a_list_or_quote_line_is_a_boundary(opener):
    doc = _doc(S, "x", E, "notes", opener, "  echo", "  ```")
    assert (doc.boundary, doc.boundary_cause) == (4, Cause.CONTAINER_FENCE)
    assert all(line.kind is Kind.UNKNOWN for line in doc.lines[4:])
    assert _session(doc).spans == ((0, 2),)


def test_two_bullet_fences_never_hide_a_pin_as_code():
    doc = _doc("- ```bash", "  a", "  ```", "### Pin B", "- ```bash", "  b", "  ```")
    assert (doc.boundary, doc.boundary_cause) == (0, Cause.CONTAINER_FENCE)
    assert doc.lines[3].kind is Kind.UNKNOWN


@pytest.mark.parametrize("line", [
    "- ```foo``` is inline", "-```bash", "    - ```", "\t- ```", "* * *",
])
def test_not_a_container_fence(line):
    doc = _doc(line, S, "x", E)
    assert doc.boundary is None
    assert _session(doc).spans == ((1, 3),)


def test_four_column_list_item_fence_holding_a_marker_is_stray():
    doc = _doc("-   Example:", "", "    ```", "    " + S, "    " + E, "    ```")
    assert doc.boundary is None
    located = _session(doc)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


def test_whole_file_pairing_sets_the_boundary_above_a_container_fence():
    # Lead ruling: pairing runs over the whole file, so the container fence's
    # indented closer, read as an opener and left unclosed, puts the boundary
    # at the file's first opener, above the real block.
    doc = _doc("notes", "```", "x", "```", S, "y", E, "- ```bash", "  echo", "  ```")
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.UNCLOSED_FENCE)
    assert _session(doc).state is State.UNKNOWN


# --- state precedence ------------------------------------------------------

def test_found_above_an_unclosed_fence_stays_found():
    doc = _doc(S, "x", E, "```", "never closed")
    assert _session(doc).spans == ((0, 2),)


def test_open_start_at_the_boundary_is_unknown_even_after_a_pair():
    doc = _doc(S, "x", E, S, "```", "never closed")
    located = _session(doc)
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.UNCLOSED_FENCE)
    assert "line 4" in located.reason


@pytest.mark.parametrize("lines, state, cause", [
    ((S, E, S, E, "```"), State.DUPLICATE, Cause.DUPLICATE),
    ((S, E, S, E, S, "```"), State.DUPLICATE, Cause.DUPLICATE),
    (("x " + S, S, E, "```"), State.MALFORMED, Cause.STRAY),
    ((S, S, E, "```"), State.MALFORMED, Cause.NESTED),
    ((E, S, E, "```"), State.MALFORMED, Cause.UNPAIRED),
])
def test_certain_row_defect_keeps_its_state_beside_an_uncertain_region(lines, state, cause):
    located = _session(_doc(*lines, "never closed"))
    assert (located.state, located.cause) == (state, cause)


@pytest.mark.parametrize("lines, state, cause", [
    ((S, "x"), State.MALFORMED, Cause.UNPAIRED),
    ((S, E, S), State.MALFORMED, Cause.UNPAIRED),
    ((E, S, E), State.MALFORMED, Cause.UNPAIRED),
    ((S, S, E, E), State.MALFORMED, Cause.NESTED),
    ((S, E, S, E), State.DUPLICATE, Cause.DUPLICATE),
])
def test_known_scope_defects(lines, state, cause):
    located = _session(_doc(*lines))
    assert (located.state, located.cause) == (state, cause)


def test_duplicate_reports_every_span():
    assert _session(_doc(S, E, "x", S, E)).spans == ((0, 1), (3, 4))


def test_no_marker_with_an_uncertain_region_is_unknown_not_absent():
    located = _session(_doc("notes", "```", "never closed"))
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.UNCLOSED_FENCE)


# --- find_marker, and headings through find_lines --------------------------

def test_find_marker_states():
    marker = PINNED_START_MARKER
    assert _doc("x", marker).find_marker(marker).spans == ((1, 1),)
    assert _doc(marker, marker).find_marker(marker).state is State.DUPLICATE
    assert _doc("x").find_marker(marker).state is State.ABSENT
    assert _doc("```", marker).find_marker(marker).state is State.UNKNOWN
    assert _doc("```", marker, "```").find_marker(marker).state is State.ABSENT
    located = _doc("see " + marker).find_marker(marker)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


@pytest.mark.parametrize("literal", ["## Pinned Context", "### Title", ""])
def test_marker_lookups_refuse_anything_but_an_html_comment(literal):
    doc = _doc("x")
    for lookup in (doc.find_marker, doc.marker_rows, lambda lit: doc.find_block(lit, E)):
        with pytest.raises(ValueError):
            lookup(literal)


_WORKING_MEMORY = re.compile(r"## Working Memory\s*$")


@pytest.mark.parametrize("lookalike", [
    "see the ## Working Memory section",
    "    ## Working Memory",
    "> ## Working Memory",
    "  ## Working Memory",
    "### Working Memory sync rule",
])
def test_heading_lookalike_is_not_the_section_and_never_malformed(lookalike):
    assert _doc("intro", lookalike).find_lines(_WORKING_MEMORY) == ()
    assert _doc("intro", lookalike, "## Working Memory").find_lines(_WORKING_MEMORY) == (2,)


def test_fenced_heading_is_not_the_section():
    doc = _doc("```", "## Working Memory", "```", "## Working Memory")
    assert doc.find_lines(_WORKING_MEMORY) == (3,)


# --- the locating API ------------------------------------------------------

def test_find_lines_matches_prose_rows_only():
    heading = re.compile(r"### ")
    assert _doc("### A", "```", "### B", "```", "### C").find_lines(heading) == (0, 4)
    unclosed = _doc("### A", "```", "### B", "```", "### C", "~~~", "### D")
    assert unclosed.find_lines(heading) == (0,)


def test_inner_offsets_and_scope():
    text = "intro\n" + S + "\nbody 1\nbody 2\n" + E + "\nafter\n"
    doc = parse(text)
    located = _session(doc)
    assert doc.inner(located) == (2, 3)
    start, end = doc.offsets(*located.spans[0])
    assert text[start:end] == S + "\nbody 1\nbody 2\n" + E + "\n"
    assert _session(doc, (0, 3)).state is State.MALFORMED
    assert doc.scope_known((0, 5))
    with pytest.raises(ValueError):
        doc.inner(_session(doc, (4, 5)))
    with pytest.raises(ValueError):
        _session(doc, (0, 6))


def test_may_hold_reads_only_the_uncertain_rows():
    # Rows 0-1 are certain; the unclosed fence on row 2 makes rows 2-4 UNKNOWN.
    doc = _doc(f"see {S} here", "intro", "```", f"x {E} y", "tail")
    assert doc.may_hold(E)  # on an UNKNOWN row, mid-line
    assert not doc.may_hold(S)  # only on a certain row
    assert not doc.may_hold("<!-- PACT_START:")  # nowhere
    assert not doc.may_hold(E, (0, 1))  # outside the scope
    assert not parse(f"{S}\n{E}\n").may_hold(E)  # every row certain


def test_find_lines_pattern_sees_content_without_its_terminator():
    doc = parse("intro\r\n## Pinned Context\r\nx\n")
    assert doc.find_lines(re.compile(r"^## Pinned Context\s*\n")) == ()
    assert doc.find_lines(re.compile(r"^## Pinned Context\s*$")) == (1,)


@pytest.mark.parametrize("lines", [(), (S, E), ("a", "b", "c")])
def test_empty_scope_is_known_and_finds_nothing(lines):
    doc = parse("".join(line + "\n" for line in lines))
    for first in range(len(doc.lines) + 1):
        scope = (first, first - 1)
        assert doc.scope_known(scope)
        assert _session(doc, scope).state is State.ABSENT
        assert doc.find_marker(S, scope).state is State.ABSENT
        assert doc.marker_rows(S, scope) == ()
        assert doc.find_lines(re.compile(""), scope) == ()
        assert doc.find_section(re.compile(""), None, scope).state is State.ABSENT


def test_empty_scope_between_adjacent_markers():
    doc = _doc(S, E, "after")
    first, last = _session(doc).spans[0]
    assert doc.inner(_session(doc)) == ()
    assert doc.find_lines(re.compile(r"after"), (first + 1, last - 1)) == ()


@pytest.mark.parametrize("scope", [(3, 2) , (-1, -2), (2, 0), (0, 3)])
def test_scope_outside_the_document_raises(scope):
    with pytest.raises(ValueError):
        _doc("a", "b").scope_known(scope)


@pytest.mark.parametrize("first, last", [(1, 0), (0, -1), (2, 1)])
def test_offsets_refuses_an_empty_range(first, last):
    with pytest.raises(ValueError):
        _doc("a", "b").offsets(first, last)


# --- find_section ----------------------------------------------------------

def _pinned(doc, scope=None, unique=False,
            terminator: "re.Pattern[str] | None" = PINNED_TERMINATOR):
    return doc.find_section(PINNED_HEADING, terminator, scope,
                            stop_prefixes=BOUNDARY_PREFIXES, unique=unique)


def _memory_interior(doc):
    first, last = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER).spans[0]
    return first + 1, last - 1


@pytest.mark.parametrize("lines, spans", [
    (("## Pinned Context", "### a", "## Working Memory", "x"), ((0, 1),)),  # terminator
    (("## Pinned Context", "### a", SESSION_START_MARKER), ((0, 1),)),  # stop prefix, exact
    # An indented marker line ends it too.
    (("## Pinned Context", "### a", "  " + MEMORY_END_MARKER, "### b"), ((0, 1),)),
    (("## Pinned Context", "### a", "### b"), ((0, 2),)),  # runs to the scope's end
    (("intro", "## Pinned Context"), ((1, 1),)),  # heading on the last row
    # Fenced terminators and stop prefixes end nothing.
    (("## Pinned Context", "```", SESSION_START_MARKER, "## x", "```", "y"), ((0, 5),)),
    # A hidden terminator still ends the section: skipping it could only lengthen the span.
    (("## Pinned Context", "### a", "<!--", "## old", "-->", "### b"), ((0, 2),)),
    (("## Pinned Context", "### a", "## Next", "```", "never closed"), ((0, 1),)),  # FOUND above
])
def test_find_section_found(lines, spans):
    located = _pinned(_doc(*lines))
    assert (located.state, located.spans, located.cause) == (State.FOUND, spans, None)


def test_find_section_stop_prefix_inside_a_closed_html_block_still_ends_it():
    doc = _doc("## Pinned Context", "### a", "<pre>", "text", PINNED_END_MARKER, "</pre>", "### b")
    assert _in_html_rows(doc) == [2, 3, 4, 5]
    assert doc.find_section(PINNED_HEADING, PINNED_TERMINATOR,
                            stop_prefixes=("<!-- PACT_MEMORY_",)).spans == ((0, 3),)


def test_find_section_heading_only_on_hidden_rows_is_unknown_commented():
    doc = _doc("intro", "<!--", "## Pinned Context", "### a", "-->", "after")
    for unique in (False, True):
        located = _pinned(doc, unique=unique)
        assert (located.state, located.spans, located.cause) == (State.UNKNOWN, (), Cause.COMMENTED)
        assert "line 3" in located.reason
    assert doc.boundary is None


def test_find_section_hidden_headings_never_count_toward_duplicate():
    doc = _doc("<!--", "## Pinned Context", "-->", "## Pinned Context", "### a")
    assert _pinned(doc, unique=True).spans == ((3, 4),)


def test_find_section_without_a_terminator_ends_only_at_a_stop_prefix_or_the_scope():
    doc = _doc("## Pinned Context", "## Working Memory", SESSION_END_MARKER, "x")
    assert _pinned(doc, terminator=None).spans == ((0, 1),)
    assert doc.find_section(PINNED_HEADING, None).spans == ((0, 3),)


def test_find_section_absent_and_unknown_without_a_heading():
    doc = _doc("intro", "## Pinned Context (old)", "see ## Pinned Context")
    assert _pinned(doc).state is State.ABSENT
    located = _pinned(_doc("intro", "```", "## Pinned Context"))
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.UNCLOSED_FENCE)
    assert "line 2" in located.reason


def test_find_section_running_into_the_boundary_is_unknown():
    located = _pinned(_doc("## Pinned Context", "### a", "```", "x"))
    assert (located.state, located.spans) == (State.UNKNOWN, ())
    assert located.cause is Cause.UNCLOSED_FENCE
    assert "line 1" in located.reason


def test_find_section_duplicate_only_with_unique():
    doc = _doc("## Pinned Context", "### a", "## Pinned Context", "### b")
    located = _pinned(doc, unique=True)
    assert (located.state, located.spans, located.cause) == (
        State.DUPLICATE, ((0, 0), (2, 2)), Cause.DUPLICATE)
    assert _pinned(doc).spans == ((0, 1),)


def test_find_section_refuses_a_stop_prefix_that_is_not_a_comment():
    with pytest.raises(ValueError):
        _doc("x").find_section(PINNED_HEADING, None, stop_prefixes=("## Working Memory",))


def test_indented_end_marker_ends_the_pinned_section():
    doc = _doc("## Pinned Context", "### a", "   " + MEMORY_END_MARKER, "### not a pin")
    assert _pinned(doc).spans == ((0, 1),)


def test_notes_heading_below_the_pins_adds_no_pin():
    doc = _doc(MEMORY_START_MARKER, "## Pinned Context", "### a", "# Notes", "### n",
               MEMORY_END_MARKER)
    located = _pinned(doc, _memory_interior(doc), unique=True)
    assert located.spans == ((1, 2),)
    assert doc.find_lines(re.compile(r"### "), located.spans[0]) == (2,)


def test_commented_out_old_pinned_section_leaves_the_real_one_found():
    doc = _doc(MEMORY_START_MARKER, "<!--", "## Pinned Context", "### old", "-->",
               "## Pinned Context", "### a", MEMORY_END_MARKER)
    assert _pinned(doc, _memory_interior(doc), unique=True).spans == ((5, 6),)


def test_heading_on_the_only_interior_row():
    doc = _doc(MEMORY_START_MARKER, "## Pinned Context", MEMORY_END_MARKER)
    assert _pinned(doc, _memory_interior(doc), unique=True).spans == ((1, 1),)


_PIN_ROWS = re.compile(r"### ")


@pytest.mark.parametrize("body, spans, pins", [
    # A: a declaration closed by a later prose `>` hides nothing.
    (("<!Note to self: keep it short", "## Pinned Context", "### a", "see -> here"), (2, 4), (3,)),
    # B: an unclosed comment opener, then `-->` used as an arrow.
    (("<!-- TODO tidy", "## Pinned Context", "### a", "flow: a --> b"), (2, 4), (3,)),
    # C: a declaration inside a pin body leaves every pin counted.
    (("## Pinned Context", "### a", "<!Note", "### b", "Map<K, V> type"), (1, 5), (2, 4)),
    # C2: a comment inside a pin body, closed at a row's end, still counts the pin in it.
    (("## Pinned Context", "### a", "<!-- TODO", "### b", "tidy later -->"), (1, 5), (2, 4)),
    # D2: a deliberate comment-out whose closer begins its row.
    (("<!--", "## Pinned Context", "### old", "--> kept for reference", "## Pinned Context",
      "### a"), (5, 6), (6,)),
])
def test_finder_rows_for_accidental_and_deliberate_html_blocks(body, spans, pins):
    doc = _doc(MEMORY_START_MARKER, *body, MEMORY_END_MARKER)
    located = _pinned(doc, _memory_interior(doc), unique=True)
    assert located.spans == (spans,)
    assert doc.find_lines(_PIN_ROWS, spans) == pins


def test_only_pinned_heading_inside_a_comment_is_unknown_commented():
    doc = _doc(MEMORY_START_MARKER, "<!--", "## Pinned Context", "### a", "-->", MEMORY_END_MARKER)
    located = _pinned(doc, _memory_interior(doc), unique=True)
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.COMMENTED)


def test_hidden_working_memory_heading_below_the_pins_still_ends_the_pinned_section():
    doc = _doc(MEMORY_START_MARKER, "## Pinned Context", "### a", "<!--", "## Working Memory",
               "### old entry", "-->", MEMORY_END_MARKER)
    assert _pinned(doc, _memory_interior(doc), unique=True).spans == ((1, 3),)


# --- contract --------------------------------------------------------------

def test_parse_needs_str():
    with pytest.raises(TypeError):
        parse(b"bytes")  # type: ignore[arg-type]


def test_module_imports_only_re_typing_and_enum():
    tree = ast.parse(Path(claude_md_markers.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"__future__", "re", "typing", "enum"}


def test_importing_the_finder_does_not_load_dataclasses():
    # A fresh, isolated interpreter: dataclasses costs every hook process
    # several milliseconds, and no hot hook loads it otherwise. The module is
    # loaded from its file, so the probe changes no import path.
    finder = str(Path(claude_md_markers.__file__).resolve())
    probe = (
        "import importlib.util, sys\n"
        "before = 'dataclasses' in sys.modules\n"
        f"spec = importlib.util.spec_from_file_location('claude_md_markers', {finder!r})\n"
        "module = sys.modules[spec.name] = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(before, 'dataclasses' in sys.modules)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", probe],
                            capture_output=True, text=True, check=True)
    assert result.stdout.split() == ["False", "False"]


def test_line_and_located_keep_their_fields_defaults_and_immutability():
    line = claude_md_markers.Line(0, 0, 2, "x", Kind.PROSE)
    assert line._fields == ("row", "start", "end", "content", "kind", "in_html")
    assert line.in_html is False
    assert claude_md_markers.Located._fields == ("state", "spans", "reason", "cause")
    with pytest.raises(AttributeError):
        line.row = 1  # type: ignore[misc]
    assert hash(line) == hash(claude_md_markers.Line(0, 0, 2, "x", Kind.PROSE))
