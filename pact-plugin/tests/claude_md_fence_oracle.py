"""A naive reference scanner for the fence grammar of PACT's CLAUDE.md finder.

Written from the plan's grammar text and the rulings on it, not from
hooks/shared/claude_md_markers.py, and it must import nothing from that module
(test_claude_md_fence_oracle.py asserts this). The property test compares the
finder with this scanner row by row, so a shared mistake would hide a wrong
parser: keep the two independent.

The grammar it implements:
- Rows end only at \\r\\n, \\r and \\n. A leading U+FEFF is not part of row 0's
  content but is counted in its offsets.
- A fence opener is up to 3 spaces, then ``` or ~~~ of length 3 or more; a
  backtick opener's info string may not contain a backtick. A closer is up to
  3 spaces, the opener's character at least the opener's length, then only
  spaces or tabs. Fences do not nest. Rows between are CODE, the two fence
  rows FENCE, everything else PROSE.
- HTML blocks of CommonMark types 1-5 suppress fence openers until their end
  condition, which is tested over the whole start line too. Their rows stay
  PROSE.
- A block of type 1, 2, 3 or 5 that is closed on a later row than it starts,
  on a row that begins or ends with its end token (only spaces or tabs around
  it), hides its rows (in_html) from start to end. A block closed on its start
  row, closed mid-line, never closed, or of type 4 hides none.
- An HTML block of any of those types whose end condition is first met on a
  row that itself starts (after up to 3 spaces) with `<!--` sets the
  uncertainty boundary at the block's start.
- A container fence sets the boundary at its row: a PROSE row of up to 3
  spaces, then one or more container markers (`>` and any spaces or tabs, or a
  list marker - * + or 1-9 digits then . or ) and at least one space or tab),
  then a fence run; after a backtick run the line holds no backtick.
- When pairing leaves a fence unclosed, the boundary is the first fence
  opener in the file.
- The boundary is the earliest of the three; every row from it on is UNKNOWN,
  and no UNKNOWN row is in_html.
"""

import re
from typing import NamedTuple, Optional, Sequence

PROSE, FENCE, CODE, UNKNOWN = "PROSE", "FENCE", "CODE", "UNKNOWN"
UNCLOSED_FENCE, CONTAINER_FENCE, COMMENT_BOUNDARY = (
    "unclosed_fence", "container_fence", "comment_boundary")

_OPENER = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$", re.DOTALL)
_CLOSER_TAIL = re.compile(r"^[ \t]*$")
_CONTAINER = re.compile(
    r"^ {0,3}(?:>[ \t]*|(?:[-*+]|[0-9]{1,9}[.)])[ \t]+)+(`{3,}|~{3,})(.*)$", re.DOTALL)
_COMMENT_START_LINE = re.compile(r"^ {0,3}<!--")

# CommonMark HTML block start conditions 1-5, each with its end marker.
_HTML_STARTS = (
    (re.compile(r"^ {0,3}<(script|pre|style|textarea)(?=[ \t>]|$)", re.IGNORECASE),
     re.compile(r"</(script|pre|style|textarea)>", re.IGNORECASE)),
    (re.compile(r"^ {0,3}<!--"), re.compile(r"-->")),
    (re.compile(r"^ {0,3}<\?"), re.compile(r"\?>")),
    (re.compile(r"^ {0,3}<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"^ {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
)


class Row(NamedTuple):
    start: int
    end: int          # includes the terminator
    content: str      # excludes the terminator, and a leading BOM on row 0
    kind: str
    in_html: bool


class Scan(NamedTuple):
    rows: tuple[Row, ...]
    boundary: Optional[int]
    cause: Optional[str]


def split_rows(text: str) -> list[tuple[int, int, str]]:
    """(start, end, content) per row; only \\r\\n, \\r and \\n end a row."""
    out = []
    i, n = 0, len(text)
    while i < n:
        j = i
        while j < n and text[j] not in "\r\n":
            j += 1
        content = text[i:j]
        if j < n and text[j] == "\r" and j + 1 < n and text[j + 1] == "\n":
            end = j + 2
        elif j < n:
            end = j + 1
        else:
            end = j
        out.append((i, end, content))
        i = end
    if out and out[0][2].startswith("﻿"):
        s, e, c = out[0]
        out[0] = (s, e, c[1:])
    return out


def _opener(content: str) -> Optional[tuple[str, int]]:
    m = _OPENER.match(content)
    if not m:
        return None
    run, info = m.group(2), m.group(3)
    if run[0] == "`" and "`" in info:
        return None
    return run[0], len(run)


def _closes(content: str, char: str, length: int) -> bool:
    m = _OPENER.match(content)
    if not m:
        return False
    run, tail = m.group(2), m.group(3)
    return run[0] == char and len(run) >= length and bool(_CLOSER_TAIL.match(tail))


def _container_fence(content: str) -> bool:
    m = _CONTAINER.match(content)
    if not m:
        return False
    run, info = m.group(1), m.group(2)
    return not (run[0] == "`" and "`" in info)


def _html_start(content: str):
    for number, (start, end) in enumerate(_HTML_STARTS, 1):
        if start.match(content):
            return number, end
    return None


def _hides(block_type: int, end: "re.Pattern[str]", content: str) -> bool:
    """Whether a block closing on this row hides its rows: never type 4, and only
    when the row, spaces and tabs aside, begins or ends with the end token."""
    edge = content.strip(" \t")
    return block_type != 4 and (
        end.match(edge) is not None or re.search(f"(?:{end.pattern})$", edge, end.flags) is not None)


def scan(text: str) -> Scan:
    rows = split_rows(text)
    kinds = [PROSE] * len(rows)
    in_html = [False] * len(rows)
    first_opener = None
    container_row = None
    comment_row = None
    fence = None            # (char, length) while inside a fence
    html_end = None         # end pattern while inside an HTML block
    html_type = 0
    html_start_row = 0
    for r, (_s, _e, content) in enumerate(rows):
        if fence is not None:
            if _closes(content, *fence):
                kinds[r] = FENCE
                fence = None
            else:
                kinds[r] = CODE
            continue
        if html_end is not None:
            if html_end.search(content):
                if _COMMENT_START_LINE.match(content) and comment_row is None:
                    comment_row = html_start_row
                if _hides(html_type, html_end, content):
                    for k in range(html_start_row, r + 1):
                        in_html[k] = True
                html_end = None
            continue
        op = _opener(content)
        if op is not None:
            kinds[r] = FENCE
            fence = op
            if first_opener is None:
                first_opener = r
            continue
        if _container_fence(content):
            if container_row is None:
                container_row = r
            continue
        started = _html_start(content)
        if started is not None and not started[1].search(content):
            html_type, html_end = started
            html_start_row = r
    candidates = []
    if fence is not None and first_opener is not None:
        candidates.append((first_opener, UNCLOSED_FENCE))
    if container_row is not None:
        candidates.append((container_row, CONTAINER_FENCE))
    if comment_row is not None:
        candidates.append((comment_row, COMMENT_BOUNDARY))
    boundary, cause = min(candidates) if candidates else (None, None)
    if boundary is not None:
        for r in range(boundary, len(rows)):
            kinds[r] = UNKNOWN
            in_html[r] = False
    return Scan(
        tuple(Row(s, e, c, k, h) for (s, e, c), k, h in zip(rows, kinds, in_html)),
        boundary, cause)


# --- locating PACT's HTML-comment markers ---------------------------------------
#
# From the plan's definitions and the rulings, written without the finder:
# - a literal starts with `<!--` (anything else is a ValueError). One that ends in
#   `-->` is exact: its marker line is a certain PROSE row of up to 3 spaces, the
#   literal, then only spaces or tabs. Any other literal is a prefix: up to 3
#   spaces, the literal, the rest of one comment up to its first `-->`, then only
#   spaces or tabs;
# - a row that is a marker line for any of the looked-up literals is never stray.
#   Any other certain PROSE row in scope holding a literal outside every inline code
#   span is stray. An opening backtick run after an odd number of backslashes loses
#   its first backtick; a closing run is taken as it stands;
# - a scope is an inclusive (first, last); (first, first - 1) with first from 0 to
#   the row count is empty and known; any other scope outside the rows is a
#   ValueError. Lookups read only the rows in scope;
# - a stray result carries one (row, row) span per marker line of the looked-up
#   literals in scope, in row order; the stray rows themselves are not spans;
# - first match wins: stray; a nested start or an end with no start, in row order;
#   a start left open in a known scope; two or more pairs or marker lines
#   (DUPLICATE); a start left open in an unknown scope (UNKNOWN); one pair or
#   marker line (FOUND); none in a known scope (ABSENT); none (UNKNOWN). UNKNOWN
#   carries the document's boundary cause.

FOUND, ABSENT, DUPLICATE, MALFORMED = "FOUND", "ABSENT", "DUPLICATE", "MALFORMED"
STRAY, UNPAIRED, NESTED, DUPLICATED, COMMENTED = "stray", "unpaired", "nested", "duplicate", "commented"


class Block(NamedTuple):
    state: str
    spans: tuple[tuple[int, int], ...]
    cause: Optional[str]


def _code_spans(content: str) -> list[tuple[int, int]]:
    """(start, end) of each inline code span's text: an opening backtick run, then
    the next run of exactly its width on the row."""
    runs = [(m.start(), m.end()) for m in re.finditer(r"`+", content)]
    spans, i = [], 0
    while i < len(runs):
        s, e = runs[i]
        before = content[:s]
        if (len(before) - len(before.rstrip("\\"))) % 2:
            s += 1
        width = e - s
        j = next((k for k in range(i + 1, len(runs)) if runs[k][1] - runs[k][0] == width), None)
        if width == 0 or j is None:
            i += 1
            continue
        spans.append((e, runs[j][0]))
        i = j + 1
    return spans


def _occurrences_outside_spans(content: str, literal: str) -> int:
    spans = _code_spans(content)
    count, at = 0, content.find(literal)
    while at != -1:
        if not any(s <= at and at + len(literal) <= e for s, e in spans):
            count += 1
        at = content.find(literal, at + 1)
    return count


def _check_literal(literal: str) -> None:
    if not literal.startswith("<!--"):
        raise ValueError(f"{literal!r} is not an HTML-comment marker")


def _is_marker_line(content: str, literal: str) -> bool:
    lead = len(content) - len(content.lstrip(" "))
    rest = content[lead:]
    if lead > 3 or not rest.startswith(literal):
        return False
    tail = rest[len(literal):]
    if not literal.endswith("-->"):
        close = tail.find("-->")
        if close == -1:
            return False
        tail = tail[close + 3:]
    return tail.strip(" \t") == ""


def scope_rows(scan: Scan, scope: Optional[tuple[int, int]]) -> range:
    n = len(scan.rows)
    if scope is None:
        return range(n)
    first, last = scope
    if first == last + 1 and 0 <= first <= n:
        return range(first, first)
    if not 0 <= first <= last < n:
        raise ValueError(f"scope {scope!r} is outside the document")
    return range(first, last + 1)


def scope_known(scan: Scan, scope: Optional[tuple[int, int]] = None) -> bool:
    return all(scan.rows[i].kind != UNKNOWN for i in scope_rows(scan, scope))


def _stray(scan: Scan, rows: range, literals: Sequence[str]) -> bool:
    for i in rows:
        content = scan.rows[i].content
        if scan.rows[i].kind != PROSE or any(_is_marker_line(content, lit) for lit in literals):
            continue
        if any(_occurrences_outside_spans(content, lit) for lit in literals):
            return True
    return False


def _marker_rows(scan: Scan, rows: range, literal: str) -> list[int]:
    return [i for i in rows if scan.rows[i].kind == PROSE and _is_marker_line(scan.rows[i].content, literal)]


def find_block(scan: Scan, start: str, end: str, scope: Optional[tuple[int, int]] = None) -> Block:
    _check_literal(start)
    _check_literal(end)
    rows = scope_rows(scan, scope)
    starts, ends = set(_marker_rows(scan, rows, start)), set(_marker_rows(scan, rows, end))
    if _stray(scan, rows, (start, end)):
        return Block(MALFORMED, tuple((i, i) for i in rows if i in starts or i in ends), STRAY)
    pairs, open_row = [], None
    for i in rows:
        if i in starts:
            if open_row is not None:
                return Block(MALFORMED, (), NESTED)
            open_row = i
        elif i in ends:
            if open_row is None:
                return Block(MALFORMED, (), UNPAIRED)
            pairs.append((open_row, i))
            open_row = None
    known = scope_known(scan, scope)
    if open_row is not None and known:
        return Block(MALFORMED, (), UNPAIRED)
    if len(pairs) >= 2:
        return Block(DUPLICATE, tuple(pairs), DUPLICATED)
    if open_row is not None:
        return Block(UNKNOWN, (), scan.cause)
    if pairs:
        return Block(FOUND, tuple(pairs), None)
    return Block(ABSENT, (), None) if known else Block(UNKNOWN, (), scan.cause)


def find_marker(scan: Scan, literal: str, scope: Optional[tuple[int, int]] = None) -> Block:
    _check_literal(literal)
    rows = scope_rows(scan, scope)
    found = _marker_rows(scan, rows, literal)
    if _stray(scan, rows, (literal,)):
        return Block(MALFORMED, tuple((i, i) for i in found), STRAY)
    if len(found) >= 2:
        return Block(DUPLICATE, tuple((i, i) for i in found), DUPLICATED)
    if found:
        return Block(FOUND, ((found[0], found[0]),), None)
    return Block(ABSENT, (), None) if scope_known(scan, scope) else Block(UNKNOWN, (), scan.cause)


def find_lines(scan: Scan, pattern: "re.Pattern[str]", scope: Optional[tuple[int, int]] = None) -> tuple[int, ...]:
    """Rows in scope that are PROSE, hidden or not, and whose content the pattern
    matches at its start."""
    return tuple(i for i in scope_rows(scan, scope)
                 if scan.rows[i].kind == PROSE and pattern.match(scan.rows[i].content))


# --- locating a section ------------------------------------------------------------
#
# The heading rows are the rows of find_lines(heading, scope) that are not hidden.
# None, but a hidden row matches: UNKNOWN, cause commented. None at all: ABSENT in a
# known scope, else UNKNOWN. With unique and two or more: DUPLICATE, one (row, row)
# per heading. Otherwise the section starts at the first heading row h and ends
# before the first later row in scope that is a PROSE row the terminator matches,
# hidden or not, or a marker line for a stop prefix. With no such row it runs to
# the scope's last row: FOUND when those rows are known, else UNKNOWN. Never
# MALFORMED.

def find_section(scan: Scan, heading: "re.Pattern[str]", terminator: "Optional[re.Pattern[str]]",
                 scope: Optional[tuple[int, int]] = None, stop_prefixes: Sequence[str] = (),
                 unique: bool = False) -> Block:
    for prefix in stop_prefixes:
        _check_literal(prefix)
    rows = scope_rows(scan, scope)
    matched = find_lines(scan, heading, scope)
    heads = [i for i in matched if not scan.rows[i].in_html]
    if not heads and matched:
        return Block(UNKNOWN, (), COMMENTED)
    if not heads:
        return Block(ABSENT, (), None) if scope_known(scan, scope) else Block(UNKNOWN, (), scan.cause)
    if unique and len(heads) >= 2:
        return Block(DUPLICATE, tuple((h, h) for h in heads), DUPLICATED)
    h, last = heads[0], rows[-1]
    for i in range(h + 1, last + 1):
        row = scan.rows[i]
        if row.kind != PROSE:
            continue
        if ((terminator is not None and terminator.match(row.content))
                or any(_is_marker_line(row.content, p) for p in stop_prefixes)):
            return Block(FOUND, ((h, i - 1),), None)
    if scope_known(scan, (h, last)):
        return Block(FOUND, ((h, last),), None)
    return Block(UNKNOWN, (), scan.cause)
