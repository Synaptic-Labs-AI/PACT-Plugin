#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/claude_md_markers.py

Summary: The one fence-aware parser and locator for PACT's markers in a
CLAUDE.md. `parse(text)` classifies every line as prose, fence, code or
unknown, and the returned `Document` answers each lookup with an explicit
state. Pure: str in, no I/O, stdlib only (`re`, `typing`, `enum`; `difflib`
only when `uncertainty_added` refuses), so every hot hook can import it; `Line` and `Located` are NamedTuples, not dataclasses,
because `dataclasses` would add its own import cost to every hook process.

Used by: every reader and writer of a PACT marker or section in a CLAUDE.md,
and by the pin-cap gate's parsers. Callers never split lines themselves: they
use rows and offsets from the `Document`, and slice content from the ORIGINAL
text by offsets.

MARKERS AND HEADINGS ARE LOCATED DIFFERENTLY. `find_block`, `find_marker`,
`marker_rows` and `find_section`'s `stop_prefixes` take only PACT's singleton
HTML-comment markers, under the marker-line and stray rules. A marker line is
up to 3 spaces, the literal, then only spaces or tabs; a literal that does not
end in `-->` is a prefix, and the rest of ONE comment ending in `-->` comes
before those spaces. Anchor a section or heading with `find_section`; read
rows inside a resolved span (entries, pins, session fields, STALE, `pinned:`,
WARNING) with `find_lines`. Neither has a stray rule or indent tolerance.
Patterns match a row's CONTENT, which never holds the terminator: write
`\\s*$`, never `\\s*\\n` (a pattern that requires `\\n` matches nothing).
`find_lines` reads every PROSE row; `find_section` takes its heading from
visible rows only, and when only a hidden row (`Line.in_html`) matches it is
UNKNOWN, cause commented, never ABSENT, so a writer refuses instead of adding
a second section.

GRAMMAR. CommonMark fenced-code rules at the top level, plus HTML blocks of
types 1-5 for fence suppression and `in_html`:
- Lines end only at \\r\\n, \\r and \\n. A leading U+FEFF is outside row 0's
  content but counted in offsets.
- An opener is up to 3 spaces, then ``` or ~~~ of length 3 or more; a backtick
  info string may not hold a backtick. A closer is up to 3 spaces, the same
  character at least the opener's length, then only spaces or tabs; no nesting.
- An HTML block of types 1-5 (`<script|pre|style|textarea`, `<!--`, `<?`,
  `<!X`, `<![CDATA[`) suppresses fence openers until its end condition, tested
  over the WHOLE start line (so `<!-->` and `<?>` end there). Its rows stay
  PROSE for marker lookups. They are hidden (`in_html`), start row to end row,
  only when the block is type 1, 2, 3 or 5, spans 2+ rows, and its end row
  begins or ends with the end token (spaces or tabs aside); a one-row,
  unclosed, mid-line-closed (`a --> b`) or type-4 block hides nothing.
  A multi-row block that hides nothing may be HTML or a stray opener in
  prose; the two readings differ only at a covered row that would open a
  fence, a container fence or another HTML block, and such a row makes the
  file uncertain (below).

UNCERTAINTY BOUNDARY. Every row from `Document.boundary` on is UNKNOWN. The
boundary is the earliest of these rows:
- unclosed_fence: when top-level pairing over the whole file leaves a fence
  unclosed, the first fence opener in the file, because sequential pairing
  cannot say which fence is the unpaired one;
- container_fence: a fence opener after a list marker or `>`, whose opener the
  top-level rule cannot see while it does see the indented closer;
- comment_boundary: the start of a multi-line HTML block of types 1-5 ended
  only by a line that itself starts with `<!--`, such as a PACT marker;
- unclosed_html: the start of an HTML block that is never closed, when a row
  it covers (the rows after its start) would, read outside any HTML block,
  open a fence, a container fence or an HTML block it does not also end;
- html_hides_fence: the start of an HTML block that closes but hides nothing
  (closed mid-line, or a declaration), when a row it covers, its end row
  included, is such a row.
A block that covers no such row stays certain.
Do not narrow the boundary or add a recovery rule: each lets an example be
read as the real block again.

STATES. A lookup reads only certain rows (those before the boundary). For
`find_block` and `find_marker`, the first of these that holds wins:
1. MALFORMED, stray: a certain PROSE row holds the literal outside a marker
   line and outside every inline code span. The reason names those rows; the
   spans are the literal's marker lines in scope, one (row, row) each;
2. MALFORMED, nested or unpaired: in row order, a start before the previous
   start's end, or an end with no start before it;
3. MALFORMED, unpaired: a start with no end while the scope is known;
4. DUPLICATE: two or more blocks or marker lines;
5. UNKNOWN: a start with no end and the scope not known, even beside a pair;
6. UNKNOWN: exactly one block or marker line, and an UNKNOWN row in scope
   holds a literal outside every inline code span: read as prose it would be
   a second copy. The reason names the first such row;
7. FOUND: exactly one block or marker line, even when the scope runs on into
   UNKNOWN rows that hold no literal;
8. ABSENT: none, and the scope is known;
9. UNKNOWN: none, and the scope is not known.
`may_hold(literal)` tells whether any UNKNOWN row contains the literal as text,
so a caller can tell an uncertain region that may hide a block from one that
cannot. `find_section` returns FOUND, ABSENT, UNKNOWN or, with `unique`, DUPLICATE
among visible headings; never MALFORMED. A literal wholly inside a line-local
inline code span is a mention, not a stray; a backtick run after an odd number
of backslashes loses its first backtick (an escaped literal) when it opens.

A writer passes the parse of the text it read and of the text it plans to
`uncertainty_added`, and writes nothing when that returns a reason: a write
must never add UNKNOWN rows.

A scope is an inclusive (first_row, last_row). (first, first - 1), with first
from 0 to the row count, is an empty scope: no rows, known, and every lookup
in it is ABSENT or empty.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import NamedTuple


class Kind(Enum):
    PROSE = "PROSE"
    FENCE = "FENCE"  # an opener or closer line
    CODE = "CODE"  # inside a closed fence
    UNKNOWN = "UNKNOWN"  # at or after Document.boundary


class State(Enum):
    FOUND = "FOUND"
    ABSENT = "ABSENT"
    DUPLICATE = "DUPLICATE"
    MALFORMED = "MALFORMED"
    UNKNOWN = "UNKNOWN"


class Cause(Enum):
    STRAY = "stray"
    UNPAIRED = "unpaired"
    NESTED = "nested"
    DUPLICATE = "duplicate"
    UNCLOSED_FENCE = "unclosed_fence"
    CONTAINER_FENCE = "container_fence"
    COMMENT_BOUNDARY = "comment_boundary"
    UNCLOSED_HTML = "unclosed_html"
    HTML_HIDES_FENCE = "html_hides_fence"
    COMMENTED = "commented"  # find_section: the only matching heading is hidden


class Line(NamedTuple):
    row: int
    start: int
    end: int  # includes the terminator
    content: str  # no terminator; row 0 excludes a leading U+FEFF
    kind: Kind
    in_html: bool = False  # hidden: see GRAMMAR; never on an UNKNOWN row


class Located(NamedTuple):
    state: State
    # inclusive (first_row, last_row): the block or marker for FOUND, each one for
    # DUPLICATE, each clean marker line (row, row) for MALFORMED cause stray;
    # empty otherwise
    spans: tuple[tuple[int, int], ...]
    reason: str  # names 1-based line numbers; empty for FOUND and ABSENT
    cause: Cause | None


_BOM = "﻿"
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_OPENER_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
# Up to 3 spaces, one or more list or quote markers, then a fence run. Any
# spaces or tabs may follow a marker: no CommonMark column arithmetic, so the
# only error is a false UNKNOWN, never a fence missed.
_CONTAINER_FENCE_RE = re.compile(
    r" {0,3}(?:>[ \t]*|(?:[-*+]|[0-9]{1,9}[.)])[ \t]+)+(?:`{3,}[^`]*|~{3,}.*)")
# (start condition matched at the line's start, end condition searched in a line)
_HTML_BLOCKS = (
    (re.compile(r" {0,3}<(?:script|pre|style|textarea)(?:[ \t>]|$)", re.I),
     re.compile(r"</(?:script|pre|style|textarea)>", re.I)),
    (re.compile(r" {0,3}<!--"), re.compile(r"-->")),
    (re.compile(r" {0,3}<\?"), re.compile(r"\?>")),
    (re.compile(r" {0,3}<![A-Za-z]"), re.compile(r">")),
    (re.compile(r" {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
)
_COMMENT_START_RE = _HTML_BLOCKS[1][0]
# What follows a prefix literal on its marker line: the rest of one comment.
_REST_OF_COMMENT_RE = re.compile(r"(?:(?!-->).)*-->[ \t]*")
# A backtick run and the backslashes directly before it.
_BACKTICK_RUN_RE = re.compile(r"(\\*)(`+)")
_BOUNDARY_TEXT = {
    Cause.UNCLOSED_FENCE: "a code fence is not closed",
    Cause.CONTAINER_FENCE: "a code fence opens on a list or quote line",
    Cause.COMMENT_BOUNDARY: "an HTML block is ended only by a line that starts a comment",
    Cause.UNCLOSED_HTML: "an HTML block is never closed",
    Cause.HTML_HIDES_FENCE: ("an HTML block that ends mid-line, or a declaration, covers a code fence "
                             "or another HTML block"),
}


def _opener(content: str) -> str | None:
    """The fence string when `content` opens a fence, else None."""
    match = _OPENER_RE.fullmatch(content)
    if match is None:
        return None
    fence, info = match.groups()
    if fence[0] == "`" and "`" in info:
        return None
    return fence


def _closes(content: str, fence: str) -> bool:
    stripped = content.lstrip(" ")
    if len(content) - len(stripped) > 3:
        return False
    run = len(stripped) - len(stripped.lstrip(fence[0]))
    return run >= len(fence) and stripped[run:].strip(" \t") == ""


def _split(text: str) -> list[tuple[int, int, str]]:
    """(start, end, content) per row; end includes the terminator."""
    rows = []
    for match in _LINE_RE.finditer(text):
        start, end = match.span()
        content = match.group(0).rstrip("\r\n")
        if start == 0 and content.startswith(_BOM):
            content = content[1:]
        rows.append((start, end, content))
    return rows


def _code_span_regions(content: str) -> list[tuple[int, int]]:
    """Inner (start, end) of each inline code span on one line.

    A backtick run opens a span closed by the next run of the same length; a
    run with no such partner is literal text, and scanning resumes after it.
    An opening run that follows an odd number of backslashes loses its first
    backtick, which is an escaped literal. A closing run is matched raw,
    because escapes do not work inside a span.
    """
    runs = [(match.start(2), match.end(2), len(match.group(1)) % 2 == 1)
            for match in _BACKTICK_RUN_RE.finditer(content)]
    regions, index = [], 0
    while index < len(runs):
        open_start, open_end, escaped = runs[index]
        width = open_end - open_start - escaped
        partner = next((j for j in range(index + 1, len(runs))
                        if runs[j][1] - runs[j][0] == width), None) if width else None
        if partner is None:
            index += 1
            continue
        regions.append((open_end, runs[partner][0]))
        index = partner + 1
    return regions


def _has_stray(content: str, literal: str) -> bool:
    """True when `literal` occurs in `content` outside every inline code span."""
    regions = None
    position = content.find(literal)
    while position != -1:
        if regions is None:
            regions = _code_span_regions(content)
        end = position + len(literal)
        if not any(lo <= position and end <= hi for lo, hi in regions):
            return True
        position = content.find(literal, position + 1)
    return False


def _require_comment(literal: str) -> None:
    if not literal.startswith("<!--"):
        raise ValueError(f"{literal!r} is not an HTML-comment marker; use find_lines")


def _lines_text(rows) -> str:
    """'line 5' or 'lines 5, 12' for 0-based rows."""
    numbers = [str(row + 1) for row in rows]
    return ("line " if len(numbers) == 1 else "lines ") + ", ".join(numbers)


class Document:
    """A parsed CLAUDE.md. Build it with `parse(text)`.

    Attributes:
        text: the original text.
        lines: one `Line` per row.
        boundary: the first UNKNOWN row, or None when every row is certain.
        boundary_cause: why `boundary` is set, or None.

    Scopes are as the module docstring says; ValueError outside the document.
    """

    def __init__(self, text: str):
        self.text = text
        rows = _split(text)
        kinds, in_html, boundary, cause = _classify([content for _, _, content in rows])
        self.boundary = boundary
        self.boundary_cause = cause
        self.lines = tuple(
            Line(row, start, end, content, kinds[row], in_html[row])
            if boundary is None or row < boundary
            else Line(row, start, end, content, Kind.UNKNOWN)
            for row, (start, end, content) in enumerate(rows)
        )

    def _rows(self, scope) -> tuple[Line, ...]:
        if scope is None:
            return self.lines
        first, last = scope
        if not 0 <= first <= last + 1 <= len(self.lines):
            raise ValueError(f"scope {scope!r} is outside rows 0-{len(self.lines) - 1}")
        return self.lines[first:last + 1]

    def scope_known(self, scope=None) -> bool:
        return all(line.kind is not Kind.UNKNOWN for line in self._rows(scope))

    def may_hold(self, literal: str, scope=None) -> bool:
        """True when an UNKNOWN row in `scope` contains `literal` anywhere: a
        lookup that read UNKNOWN may have missed it there. Certain rows are not
        read; a lookup has already judged them."""
        return any(line.kind is Kind.UNKNOWN and literal in line.content
                   for line in self._rows(scope))

    def _is_marker_line(self, line: Line, literal: str) -> bool:
        if line.kind is not Kind.PROSE:
            return False
        stripped = line.content.lstrip(" ")
        if len(line.content) - len(stripped) > 3 or not stripped.startswith(literal):
            return False
        rest = stripped[len(literal):]
        if literal.endswith("-->"):
            return rest.strip(" \t") == ""
        return _REST_OF_COMMENT_RE.fullmatch(rest) is not None

    def marker_rows(self, literal: str, scope=None) -> tuple[int, ...]:
        """Rows that are a marker line for `literal` (exact or prefix). ValueError
        unless `literal` is an HTML comment; locate headings with `find_lines`."""
        _require_comment(literal)
        return tuple(line.row for line in self._rows(scope)
                     if self._is_marker_line(line, literal))

    def _stray_rows(self, literals: tuple[str, ...], scope) -> tuple[int, ...]:
        """PROSE rows holding one of `literals` outside a marker line of any
        of them and outside every inline code span."""
        return tuple(line.row for line in self._rows(scope)
                     if line.kind is Kind.PROSE
                     and not any(self._is_marker_line(line, literal) for literal in literals)
                     and any(_has_stray(line.content, literal) for literal in literals))

    def _stray(self, rows: tuple[int, ...], marker_rows: tuple[int, ...]) -> Located:
        """MALFORMED stray: the reason names the stray `rows`; the spans are the
        clean `marker_rows`, the lines that take effect once the strays are fixed."""
        return Located(State.MALFORMED, tuple((row, row) for row in sorted(set(marker_rows))),
                       f"marker text on {_lines_text(rows)} is not a marker line: it is "
                       f"indented, quoted, list-prefixed or shares its line", Cause.STRAY)

    def _uncertain_copy(self, literals: tuple[str, ...], scope) -> Located | None:
        """UNKNOWN when an UNKNOWN row in `scope` holds one of `literals` outside
        every inline code span: read as prose it is another copy, so a single
        certain match cannot be told from a duplicate. None otherwise."""
        for line in self._rows(scope):
            if line.kind is Kind.UNKNOWN:
                for literal in literals:
                    if _has_stray(line.content, literal):
                        return self._unknown(f"line {line.row + 1} may hold another {literal!r}; "
                                             f"{self._boundary_reason()}")
        return None

    def _unknown(self, reason: str) -> Located:
        return Located(State.UNKNOWN, (), reason, self.boundary_cause)

    def _boundary_reason(self) -> str:
        if self.boundary is None or self.boundary_cause is None:
            return ""
        return (f"line {self.boundary + 1} starts an uncertain region: "
                f"{_BOUNDARY_TEXT[self.boundary_cause]}")

    def find_marker(self, literal: str, scope=None) -> Located:
        """Locate a single HTML-comment marker line."""
        rows = self.marker_rows(literal, scope)
        strays = self._stray_rows((literal,), scope)
        if strays:
            return self._stray(strays, rows)
        if len(rows) > 1:
            return Located(State.DUPLICATE, tuple((row, row) for row in rows),
                           f"{literal!r} appears on {_lines_text(rows)}", Cause.DUPLICATE)
        if rows:
            return (self._uncertain_copy((literal,), scope)
                    or Located(State.FOUND, ((rows[0], rows[0]),), "", None))
        if self.scope_known(scope):
            return Located(State.ABSENT, (), "", None)
        return self._unknown(self._boundary_reason())

    def find_block(self, start_literal: str, end_literal: str, scope=None) -> Located:
        """Locate the block a start and an end marker line enclose."""
        events = sorted([(row, True) for row in self.marker_rows(start_literal, scope)]
                        + [(row, False) for row in self.marker_rows(end_literal, scope)])
        strays = self._stray_rows((start_literal, end_literal), scope)
        if strays:
            return self._stray(strays, tuple(row for row, _ in events))
        pairs, open_row = [], None
        for row, is_start in events:
            if is_start and open_row is not None:
                return Located(State.MALFORMED, (),
                               f"{start_literal!r} on line {row + 1} starts a block inside the "
                               f"block started on line {open_row + 1}", Cause.NESTED)
            if is_start:
                open_row = row
            elif open_row is None:
                return Located(State.MALFORMED, (),
                               f"{end_literal!r} on line {row + 1} has no start marker before it",
                               Cause.UNPAIRED)
            else:
                pairs.append((open_row, row))
                open_row = None
        known = self.scope_known(scope)
        if open_row is not None and known:
            return Located(State.MALFORMED, (),
                           f"{start_literal!r} on line {open_row + 1} has no end marker after it",
                           Cause.UNPAIRED)
        if len(pairs) > 1:
            return Located(State.DUPLICATE, tuple(pairs),
                           f"{len(pairs)} blocks, starting on {_lines_text(p[0] for p in pairs)}",
                           Cause.DUPLICATE)
        if open_row is not None:
            return self._unknown(f"{start_literal!r} on line {open_row + 1} has no end marker "
                                 f"before the uncertain region; {self._boundary_reason()}")
        if pairs:
            return (self._uncertain_copy((start_literal, end_literal), scope)
                    or Located(State.FOUND, tuple(pairs), "", None))
        if known:
            return Located(State.ABSENT, (), "", None)
        return self._unknown(self._boundary_reason())

    def find_lines(self, pattern: re.Pattern, scope=None) -> tuple[int, ...]:
        """Rows whose content (no terminator) `pattern.match`es, on every PROSE
        row, hidden or not. The way to read entries, pins, session fields and
        per-pin comments inside a resolved span; anchor headings with
        `find_section`."""
        return tuple(line.row for line in self._rows(scope)
                     if line.kind is Kind.PROSE and pattern.match(line.content))

    def find_section(self, heading: re.Pattern, terminator: re.Pattern | None, scope=None,
                     *, stop_prefixes: tuple[str, ...] = (), unique: bool = False) -> Located:
        """Locate a section: its first heading row through the row before its end.

        Headings are the visible (not `in_html`) rows of `find_lines(heading,
        scope)`; a heading found only on hidden rows is UNKNOWN, cause
        commented. With `unique`, two or more visible headings are DUPLICATE.
        The section ends before the first later row in scope that is a PROSE
        row `terminator` matches, hidden or not (skipping a hidden terminator
        could only lengthen the span), or a marker line for a stop prefix;
        with neither, it runs to the scope's last row when those are known.
        """
        for prefix in stop_prefixes:
            _require_comment(prefix)
        matches = self.find_lines(heading, scope)
        headings = tuple(row for row in matches if not self.lines[row].in_html)
        if not headings:
            if matches:
                return Located(State.UNKNOWN, (),
                               f"the only {heading.pattern!r} heading, on {_lines_text(matches)}, "
                               f"is inside an HTML block", Cause.COMMENTED)
            if self.scope_known(scope):
                return Located(State.ABSENT, (), "", None)
            return self._unknown(self._boundary_reason())
        first = headings[0]
        if unique and len(headings) > 1:
            return Located(State.DUPLICATE, tuple((row, row) for row in headings),
                           f"{heading.pattern!r} matches {_lines_text(headings)}",
                           Cause.DUPLICATE)
        last = len(self.lines) - 1 if scope is None else scope[1]
        for line in self._rows((first + 1, last)):
            ends = (terminator is not None and line.kind is Kind.PROSE
                    and terminator.match(line.content))
            if ends or any(self._is_marker_line(line, prefix) for prefix in stop_prefixes):
                return Located(State.FOUND, ((first, line.row - 1),), "", None)
        if self.scope_known((first, last)):
            return Located(State.FOUND, ((first, last),), "", None)
        return self._unknown(f"the section on line {first + 1} has no end before the "
                             f"uncertain region; {self._boundary_reason()}")

    def inner(self, located: Located) -> tuple[int, ...]:
        """Rows strictly between a FOUND pair. ValueError on any other state."""
        if located.state is not State.FOUND:
            raise ValueError(f"inner() needs a FOUND result, not {located.state.value}")
        first, last = located.spans[0]
        return tuple(range(first + 1, last))

    def row_start(self, row: int) -> int:
        """Where `row`'s content starts in the original text: after a leading
        U+FEFF, which `lines[0].start` counts and which stays put."""
        return self.lines[row].start + (1 if row == 0 and self.text.startswith(_BOM) else 0)

    def offsets(self, first_row: int, last_row: int) -> tuple[int, int]:
        """(start, end) of the rows in the original text, terminator included.
        ValueError on an empty range: insert at `lines[row].start` instead."""
        self._rows((first_row, last_row))
        if first_row > last_row:
            raise ValueError(f"offsets() needs at least one row, not {first_row}-{last_row}")
        return self.lines[first_row].start, self.lines[last_row].end


def _token_at_edge(content: str, end_re: re.Pattern) -> bool:
    """True when the row begins or ends with an end token (spaces or tabs
    aside). A deliberate closer sits at a row's edge; one mid-line, as in
    `a --> b`, may be an arrow, and then the block hides nothing."""
    edge = content.strip(" \t")
    return any(match.start() == 0 or match.end() == len(edge) for match in end_re.finditer(edge))


def _starts_structure(content: str) -> bool:
    """True when `content`, read outside any HTML block, would open a fence, a
    container fence, or an HTML block it does not also close."""
    if _opener(content) is not None or _CONTAINER_FENCE_RE.fullmatch(content):
        return True
    for start_re, end_re in _HTML_BLOCKS:
        if start_re.match(content):
            return not end_re.search(content)
    return False


def _classify(contents: list[str]) -> tuple[list[Kind], list[bool], int | None, Cause | None]:
    """One sequential pass: each row's kind and in_html flag, and the boundary."""
    kinds = []
    in_html = [False] * len(contents)
    fence = None  # the open fence string, while inside a fence
    html_end = None  # the end condition, while inside an HTML block
    html_type = 0  # the open HTML block's type, 1-5
    html_start = 0
    # Whether a row the open HTML block covers would start a structure if the
    # block's opener were read as prose: where its two readings differ.
    html_covers = False
    first_opener = None
    candidates = []  # (row, cause)
    for row, content in enumerate(contents):
        if fence is not None:
            if _closes(content, fence):
                fence = None
                kinds.append(Kind.FENCE)
            else:
                kinds.append(Kind.CODE)
            continue
        kinds.append(Kind.PROSE)
        if html_end is not None:
            html_covers = html_covers or _starts_structure(content)
            if html_end.search(content):
                if _COMMENT_START_RE.match(content):
                    candidates.append((html_start, Cause.COMMENT_BOUNDARY))
                # A declaration ends at any `>`, so an accidental `<!X` line
                # would hide honest rows: type 4 never hides.
                if html_type != 4 and _token_at_edge(content, html_end):
                    in_html[html_start:row + 1] = [True] * (row + 1 - html_start)
                elif html_covers:
                    candidates.append((html_start, Cause.HTML_HIDES_FENCE))
                html_end = None
            continue
        opened = _opener(content)
        if opened is not None:
            fence = opened
            kinds[row] = Kind.FENCE
            if first_opener is None:
                first_opener = row
            continue
        if _CONTAINER_FENCE_RE.fullmatch(content):
            candidates.append((row, Cause.CONTAINER_FENCE))
            continue
        for html_kind, (start_re, end_re) in enumerate(_HTML_BLOCKS, 1):
            if start_re.match(content):
                if not end_re.search(content):
                    html_end, html_type, html_start, html_covers = end_re, html_kind, row, False
                break
    if fence is not None:
        candidates.append((first_opener, Cause.UNCLOSED_FENCE))
    if html_end is not None and html_covers:
        candidates.append((html_start, Cause.UNCLOSED_HTML))
    if not candidates:
        return kinds, in_html, None, None
    row, cause = min(candidates, key=lambda candidate: candidate[0])
    return kinds, in_html, row, cause


def parse(text: str) -> Document:
    """Parse CLAUDE.md text. Never raises on str input; TypeError otherwise."""
    return Document(text)


def uncertainty_added(before: Document, after: Document) -> str | None:
    """Why a writer must not replace `before` with `after`, or None.

    `before` is the parse of the text the writer read and `after` the parse of
    the text it plans to write. The plan is refused when it holds more UNKNOWN
    rows: the write would make or widen a region PACT cannot read. A write
    above an existing boundary moves that boundary down without adding a row,
    so it is allowed.

    The reason names the boundary as a line of the file on disk: the rows of
    the two texts are matched, so rows the writer adds above or below it do
    not move the number. A boundary on a row the writer adds is named by the
    line it follows.
    """
    if after.boundary is None or after.boundary_cause is None:
        return None
    unknown = sum(line.kind is Kind.UNKNOWN for line in after.lines)
    if unknown <= sum(line.kind is Kind.UNKNOWN for line in before.lines):
        return None
    import difflib  # only on a refusal, so a write that goes ahead does not load it

    cause = _BOUNDARY_TEXT[after.boundary_cause]
    matcher = difflib.SequenceMatcher(None, [line.content for line in before.lines],
                                      [line.content for line in after.lines], autojunk=False)
    boundary = after.boundary
    tag, i1, _, j1, _ = next(op for op in matcher.get_opcodes() if op[3] <= boundary < op[4])
    if tag == "equal":
        return (f"the update would make line {i1 + boundary - j1 + 1} start a region "
                f"PACT cannot read: {cause}")
    where = f"after line {i1}" if i1 else "at the start of the file"
    return f"the update would write a line {where} that starts a region PACT cannot read: {cause}"
