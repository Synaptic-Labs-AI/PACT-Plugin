#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/pin_growth.py

Summary: The pin-growth rule. Two CLAUDE.md texts in, how many pins the
change ADDED to the Pinned section out. Pure: no I/O. The pin-cap gate
refuses a change only when it adds pins past the cap, so a rename, a move, a
fenced snippet that holds `### ` lines, or a reveal of a pin hidden by a stray
fence, an indented heading or PACT's pinned end marker is never counted as
growth.

Used by: pin_caps_gate (lazily, on the CLAUDE.md path only) and
claude_md_drift (lazily, when a file's hash changed), both through
`pin_cap_decision`. Nothing else imports it, so the parser's other importers
never load `difflib` at import; `claude_md_markers.uncertainty_added` loads it
only when a writer's planned text would add rows PACT cannot read.

THE RULE. S is the Pinned body after the change, from the one Pinned locator.
R is the matching region before: the Pinned body when the text before has one,
extended by `clause_past_stray_heading` over pins a fixed stray heading reveals
and by `clause_past_end_marker` over pins a moved pinned end marker reveals,
otherwise `clause_region_r`. The two texts are aligned line by line, once, over
the memory block only (the whole text when either side's block is not
FOUND), with shared leading and trailing lines trimmed first.

    growth = (`### ` lines in S) - (`### ` lines in R)      both fence-blind
             + withheld
             - (fenced `### ` lines in S that no clause pairs with R)
             - clause_unindented_heads
             + clause_no_leaving_credit

A `### ` line in S whose indentation the change removed is not new
(`clause_unindented_heads`). `exclude` names the candidate lines this agent's
own changes added (`reveal_candidates`): an excluded indented line earns no
pairing, and an excluded head below the end marker counts as `withheld`.

A fenced `### ` line in S is not new when a clause pairs it with R: it is
intact in place, re-fenced, a guarded aligned pairing, part of a moved block,
or edited in place. A `### ` line that LEAVES a fenced block earns no credit,
because it may be a pin hidden by a stray fence, and removing a pin must free
its slot.

Each clause is a module-level function, called by its bare name at call time,
so a mutation test can replace one by monkeypatching this module.

THE BOUND. The alignment runs through `CountedMatcher`, which counts one step
per outer row and per inner step of `find_longest_match`; the moved-block,
re-fenced and edited-in-place searches count one step per line they compare,
and the row passes one step per row. Past `STEP_BUDGET` steps the rule raises
`SizeBound`, and the caller allows with the size advisory. A bound only ever
allows.

THE LIMITS. Every part of the pin-cap decision counts its work in a `_Budget`
named for the part, and stops at its own limit in `LIMITS`: this rule, the
per-pin size rule, and the pin-cap gate's override mapping, contest and
matching. The limits are sized by what a step of each part costs, so that all
of them together are spent well inside `TIMER_SECONDS` on a slow machine, and a
large file stops at the same step on any machine. A text pair over `MAX_CHARS`
characters stops before it is parsed (`check_size`). Two timers stay as
backstops (`run_with_timer`): `TIMER_SECONDS` of this process's own processor
time, which other work on the machine cannot use up, and `WALL_SECONDS` of
wall time, which ends a wait that uses no processor time before the hook's
own timeout in hooks.json.

THE DECISION. `pin_cap_decision` is the one verdict for the gate and the Bash
report: no Pinned section anywhere after the change allows plainly, and one
PACT cannot locate allows with an advisory; growth above zero with more than
the cap's pins after it denies on count, unless `credit.count` covers it; with
a located section before, the size axis checks each new or grown pin's size
on its own (`pin_caps.size_violation`) and allows when the section's pins, the
pins over R, or the section's pins plus `credit.removed`'s pins that none of
them continues allow, and with none it compares the worst pins before and
after; the size check, the budgets, the timers and any failure allow with an
advisory. It never raises. `credit` (count, removed, exclude) holds what this
agent's earlier allowed changes removed and added; it can only turn a refusal
into an allow, and None gives none.
"""

from __future__ import annotations

import bisect
import difflib
import re
import signal
from collections import Counter
from typing import NamedTuple

from pin_caps import (PIN_COUNT_CAP, Pin, _charge, compute_deny_reason, continues, pins_in_rows, section_pins,
                      size_violation)

from .claude_md_manager import MEMORY_END_MARKER, MEMORY_START_MARKER
from .claude_md_markers import Document, Kind, Located, State, _closes, _opener, parse

TIMER_SECONDS = 30  # of this process's processor time
WALL_SECONDS = 540  # of wall time; under the pin-cap gate's timeout in hooks.json

# The parts of the pin-cap decision, as a stop note names them.
GROWTH_RULE = "the pin-growth rule"
SIZE_RULE = "the pin-size rule"
MAPPING = "the pin mapping"
CONTEST = "the override contest"
MATCHING = "the override matching"

# Each part's step limit. A step of the override check or the size rule costs
# several of the alignment's, so their limits are smaller: all five together
# take about 7 s on the slowest CI runner, under a quarter of the timer, and
# each is hundreds of times what any ordinary edit of a real file spends.
STEP_BUDGET = 12_000_000
LIMITS = {
    GROWTH_RULE: STEP_BUDGET,
    SIZE_RULE: 1_400_000,
    MAPPING: 1_500_000,
    CONTEST: 1_500_000,
    MATCHING: 1_000_000,
}

# Before plus after, in characters: past it the decision stops before parsing.
MAX_CHARS = 1_000_000

PACT_SECTIONS = ("## Working Memory", "## Retrieved Context")
_PACT_SECTION_ROW = re.compile("(?:" + "|".join(map(re.escape, PACT_SECTIONS)) + r")[ \t]*$")
_PINNED_HEADING_TEXT = "## Pinned Context"
_FENCE_SHAPE = re.compile(r" {0,3}(`{3,}|~{3,})")


class SizeBound(Exception):
    """The rule ran past its step budget, or a timer fired."""


class Texts(NamedTuple):
    """One call's view of the two texts, shared by every clause."""

    pre: Document
    post: Document
    a: list[str]  # pre row contents, trailing spaces and tabs stripped
    b: list[str]  # post row contents, the same
    p2q: dict[int, int]  # aligned rows, pre to post
    q2p: dict[int, int]  # aligned rows, post to pre
    S: tuple[int, int]  # inclusive post rows of the Pinned body, heading excluded
    budget: _Budget
    fence: dict[int, tuple[int, int]]  # post CODE row -> (opener row, closer row)
    cache: dict  # per-call memo for the guarded pairing's two sets


class Claims(NamedTuple):
    """What the moved-block and edited-in-place clauses have used up."""

    runs: set  # (start, length) of pre runs credited to a moved block
    blocks: set  # (opener, closer) of post blocks credited as moved
    rows: set  # pre rows used by a moved run or an in-place pairing


class _Budget:
    """The steps one part of the decision may spend: `limit`, else the part's
    own limit in `LIMITS`. Past it, `spend` raises `SizeBound` naming the part."""

    __slots__ = ("limit", "part", "spent")

    def __init__(self, limit: int | None = None, part: str = GROWTH_RULE):
        self.limit = LIMITS[part] if limit is None else limit
        self.part = part
        self.spent = 0

    def spend(self, steps: int) -> None:
        self.spent += steps
        if self.spent > self.limit:
            raise SizeBound(f"{self.part} passed its step budget of {self.limit}")


def check_size(before: str, after: str) -> None:
    """Raise `SizeBound` when the two texts hold more than `MAX_CHARS`
    characters together, before anything parses them."""
    chars = len(before) + len(after)
    if chars > MAX_CHARS:
        raise SizeBound(f"the file is too large for the pin cap check ({chars} characters "
                        f"before and after the change, over {MAX_CHARS})")


class CountedMatcher(difflib.SequenceMatcher):
    """`SequenceMatcher(autojunk=False)` whose `find_longest_match` spends one
    budget step per outer row and per inner step, checked after each outer row.

    The body is the CPython one (identical in 3.9 and 3.14) with the counter
    added; the stdlib's own `get_matching_blocks` drives it. A test compares
    its opcodes with the stdlib's on every supported Python.
    """

    def __init__(self, a, b, budget: _Budget):
        self.budget = budget
        super().__init__(None, a, b, autojunk=False)

    def find_longest_match(self, alo=0, ahi=None, blo=0, bhi=None):
        # set by SequenceMatcher.set_seqs; the stubs do not declare them
        a, b, b2j, isbjunk = self.a, self.b, self.b2j, self.bjunk.__contains__  # pyright: ignore[reportAttributeAccessIssue]
        spend = self.budget.spend
        if ahi is None:
            ahi = len(a)
        if bhi is None:
            bhi = len(b)
        besti, bestj, bestsize = alo, blo, 0
        j2len = {}
        nothing = []
        for i in range(alo, ahi):
            j2lenget = j2len.get
            newj2len = {}
            steps = 1
            for j in b2j.get(a[i], nothing):
                steps += 1
                if j < blo:
                    continue
                if j >= bhi:
                    break
                k = newj2len[j] = j2lenget(j - 1, 0) + 1
                if k > bestsize:
                    besti, bestj, bestsize = i - k + 1, j - k + 1, k
            j2len = newj2len
            spend(steps)
        while besti > alo and bestj > blo and \
                not isbjunk(b[bestj - 1]) and \
                a[besti - 1] == b[bestj - 1]:
            besti, bestj, bestsize = besti - 1, bestj - 1, bestsize + 1
        while besti + bestsize < ahi and bestj + bestsize < bhi and \
                not isbjunk(b[bestj + bestsize]) and \
                a[besti + bestsize] == b[bestj + bestsize]:
            bestsize += 1
        while besti > alo and bestj > blo and \
                isbjunk(b[bestj - 1]) and \
                a[besti - 1] == b[bestj - 1]:
            besti, bestj, bestsize = besti - 1, bestj - 1, bestsize + 1
        while besti + bestsize < ahi and bestj + bestsize < bhi and \
                isbjunk(b[bestj + bestsize]) and \
                a[besti + bestsize] == b[bestj + bestsize]:
            bestsize = bestsize + 1
        return difflib.Match(besti, bestj, bestsize)


def locate_pinned(doc: Document) -> Located:
    """The Pinned section, spans ((heading row, last body row),), through the
    one Pinned locator, with the heading unique because the count decides the
    cap."""
    from staleness import locate_pinned as locate

    return locate(doc, unique=True)


def _is_head(content: str) -> bool:
    return content.startswith("### ")


def _memory_range(before: Document, after: Document) -> tuple[tuple[int, int], tuple[int, int]]:
    """The rows each side aligns over: its memory block, markers included,
    when both blocks are FOUND; otherwise the whole text on both sides."""
    ranges = []
    for doc in (before, after):
        memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
        if memory.state is not State.FOUND:
            return (0, len(before.lines) - 1), (0, len(after.lines) - 1)
        ranges.append(memory.spans[0])
    return ranges[0], ranges[1]


def _align(a: list[str], b: list[str], budget: _Budget, trim: bool,
           ranges: tuple[tuple[int, int], tuple[int, int]]) -> tuple[dict, dict]:
    """Pair rows across the two texts over `ranges` (see `_memory_range`).
    Rows outside the ranges stay unaligned."""
    (ps, pe), (qs, qe) = ranges
    old, new = a[ps:pe + 1], b[qs:qe + 1]
    head = tail = 0
    if trim:
        while head < len(old) and head < len(new) and old[head] == new[head]:
            head += 1
        while (tail < len(old) - head and tail < len(new) - head
               and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]):
            tail += 1
    p2q, q2p = {}, {}

    def pair(i, j, size):
        for k in range(size):
            p2q[i + k] = j + k
            q2p[j + k] = i + k

    pair(ps, qs, head)
    pair(ps + len(old) - tail, qs + len(new) - tail, tail)
    matcher = CountedMatcher(old[head:len(old) - tail], new[head:len(new) - tail], budget)
    for i, j, size in matcher.get_matching_blocks():
        pair(ps + head + i, qs + head + j, size)
    return p2q, q2p


def _fence_pairs(doc: Document) -> dict[int, tuple[int, int]]:
    """Each CODE row's enclosing (opener, closer), in one pass."""
    out, opener, inner = {}, None, []
    for line in doc.lines:
        if line.kind is Kind.FENCE:
            if opener is None:
                opener = line.row
            else:
                for row in inner:
                    out[row] = (opener, line.row)
                opener, inner = None, []
        elif line.kind is Kind.CODE:
            inner.append(line.row)
    return out


def clause_region_r(t: Texts) -> tuple[int, int]:
    """R when the text before has no located Pinned body. It never starts
    later or ends earlier than the true section, so it errs toward allowing.

    Start: after the first `## Pinned Context` line that follows the first
    memory start line, or after the first `## Pinned Context` line when none
    follows it, skipping hidden (`in_html`) rows either way, so a user's own
    Pinned heading above PACT's block does not start R. The last memory end
    line after the start is a ceiling:
    R ends before the last line up to it equal to the line after S that is not
    a fenced literal after the change; else before the last PACT section
    heading above it that is still a section after the change; else before the
    ceiling. With no memory end line after the start, the same two searches run
    to the end of the text, which ends R when both fail. A PACT section heading
    inside R that was deleted and reappears as new prose outside S ends R before
    the last such heading instead. (0, -1) when there is no heading.

    The ceiling keeps a line below the memory block (a user's own Working
    Memory heading, or a fenced literal the alignment cannot see there) from
    stretching R over PACT's entries."""
    pre, post, a, b, p2q, q2p = t.pre, t.post, t.a, t.b, t.p2q, t.q2p
    s, e = t.S
    starts = [line.row for line in pre.lines
              if not line.in_html and line.content.strip() == _PINNED_HEADING_TEXT]
    if not starts:
        return 0, -1
    memory_start = next((line.row for line in pre.lines
                         if line.content.strip() == MEMORY_START_MARKER), None)
    floored = [row for row in starts if memory_start is not None and row > memory_start]
    lo = (floored or starts)[0] + 1
    term = b[e + 1] if e + 1 < len(b) else None
    outside = [j for j in range(len(b))
               if j not in q2p and post.lines[j].kind is Kind.PROSE and not s <= j <= e]
    new_prose = {b[j] for j in outside}

    def is_literal(i):
        j = p2q.get(i)
        return j is not None and post.lines[j].kind is not Kind.PROSE

    def is_real_section(i):
        j = p2q.get(i)
        if j is not None:
            return post.lines[j].kind is Kind.PROSE and not s <= j <= e
        return a[i] in new_prose

    ceilings = [i for i in range(lo, len(a)) if pre.lines[i].content.strip() == MEMORY_END_MARKER]
    # the ceiling row itself stays a candidate, so a memory end line that ends S ends R there
    limit = ceilings[-1] + 1 if ceilings else len(a)
    ends = [i for i in range(lo, limit) if term is not None and a[i] == term and not is_literal(i)]
    if not ends:
        ends = [i for i in range(lo, limit) if a[i] in PACT_SECTIONS and is_real_section(i)]
    if not ends:
        ends = ceilings[-1:]
    hi = ends[-1] - 1 if ends else len(a) - 1
    moved_to = {b[j] for j in outside if post.lines[j].content.startswith("## ")}
    moved = [i for i in range(lo, hi + 1)
             if i not in p2q and a[i] in PACT_SECTIONS and a[i] in moved_to]
    return (lo, moved[-1] - 1) if moved else (lo, hi)


def _pinned_scope_end(doc: Document) -> int:
    """The last row the Pinned locator reads in `doc`, whose section it found:
    the pinned pair's last interior row, or the memory block's."""
    from staleness import PINNED_END_MARKER, PINNED_START_MARKER

    first, last = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER).spans[0]
    pair = doc.find_block(PINNED_START_MARKER, PINNED_END_MARKER, (first + 1, last - 1))
    if pair.state is State.FOUND:
        return pair.spans[0][1] - 1
    return last - 1


def clause_past_stray_heading(t: Texts, R: tuple[int, int], end: int | None = None) -> tuple[int, int]:
    """R, the Pinned body before, extended past each `#` or `##` line that ends
    it, other than PACT's own section headings, while the section that line
    starts has a non-blank row aligned into S (`_holds_a_row_of_s`). Demoting,
    deleting or fencing a stray heading below Pinned reveals the pins under
    it, and they were there before. A marker line or a PACT section heading
    always ends R. The search stops at row `end`, the Pinned locator's scope
    end when None."""
    from staleness import _PINNED_STOP_PREFIXES, _PINNED_TERMINATOR

    pre, (s, e) = t.pre, t.S
    first, last = R
    end = _pinned_scope_end(pre) if end is None else end
    while last < end:
        row = last + 1
        if not pre.find_lines(_PINNED_TERMINATOR, (row, row)) or pre.find_lines(_PACT_SECTION_ROW, (row, row)):
            break
        heading = re.compile(re.escape(pre.lines[row].content) + r"\s*$")
        t.budget.spend(end - row + 1)
        section = pre.find_section(heading, _PINNED_TERMINATOR, (row, end), stop_prefixes=_PINNED_STOP_PREFIXES)
        if section.state is not State.FOUND or section.spans[0][0] != row:
            break
        below = section.spans[0][1]
        if not _holds_a_row_of_s(t, row + 1, below):
            break
        last = below
    return first, last


def _last_row_of_s(t: Texts, first: int, last: int) -> int | None:
    """The last non-blank pre row from `first` to `last` aligned into S, or
    None. A blank line carries no identity, so blanks alone never show that a
    run's lines are still there."""
    s, e = t.S
    for k in range(last, first - 1, -1):
        if s <= t.p2q.get(k, -1) <= e and t.a[k].strip():
            return k
    return None


def _holds_a_row_of_s(t: Texts, first: int, last: int) -> bool:
    """A non-blank pre row from `first` to `last` is aligned into S."""
    return _last_row_of_s(t, first, last) is not None


def _pinned_pair(doc: Document) -> tuple[int, int] | None:
    """The pinned pair's end marker row and the memory block's last interior
    row, when both are FOUND in `doc`; None otherwise."""
    from staleness import PINNED_END_MARKER, PINNED_START_MARKER

    memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
    if memory.state is not State.FOUND:
        return None
    first, last = memory.spans[0]
    pair = doc.find_block(PINNED_START_MARKER, PINNED_END_MARKER, (first + 1, last - 1))
    if pair.state is not State.FOUND:
        return None
    return pair.spans[0][1], last - 1


def clause_past_end_marker(t: Texts, R: tuple[int, int],
                           exclude=frozenset()) -> tuple[tuple[int, int], int]:
    """R extended past PACT's pinned end marker, and the heads of the extension
    `exclude` withholds. When R ends on the row before the pair's end marker,
    the run below the marker, up to the row before the first `#`/`##` line, PACT
    marker line or the memory block's end, joins R through its last non-blank
    row aligned into S (`_last_row_of_s`) and the blank rows after it: moving
    the marker down past pins reveals them, and they were there before, while
    the rows the marker still stands above stay out. When R reaches the run's
    end, `clause_past_stray_heading` continues to the memory block's end. A
    head of the extension whose text is in `exclude`, a line this agent's own
    changes put below the marker, is withheld: it counts as new."""
    pre = t.pre
    first, last = R
    pair = _pinned_pair(pre)
    if pair is None or last != pair[0] - 1:
        return R, 0
    marker, scope_end = pair
    run_end = _below_marker(pre, marker, scope_end, budget=t.budget)
    reach = _last_row_of_s(t, marker + 1, run_end)
    if reach is None:
        return R, 0
    while reach < run_end and not t.a[reach + 1].strip():
        reach += 1
    extended = clause_past_stray_heading(t, (first, reach), scope_end)
    t.budget.spend(extended[1] - marker)
    withheld = sum(1 for k in range(marker + 1, extended[1] + 1)
                   if _is_head(pre.lines[k].content) and t.a[k] in exclude)
    return extended, withheld


def _below_marker(doc: Document, marker: int, scope_end: int, *, past_stray_headings: bool = False,
                  budget: _Budget | None = None) -> int:
    """The last row of the run below the end marker on row `marker`, `marker`
    when the run is empty: the row before the first later `#`/`##` line or
    PACT marker line, as the Pinned locator ends a section, or with
    `past_stray_headings` the row before the first PACT section heading or
    PACT marker line; else `scope_end`."""
    from staleness import _PINNED_STOP_PREFIXES, _PINNED_TERMINATOR

    if budget is not None:
        budget.spend(scope_end - marker + 1)
    terminator = _PACT_SECTION_ROW if past_stray_headings else _PINNED_TERMINATOR
    heading = re.compile(re.escape(doc.lines[marker].content) + r"\s*$")
    section = doc.find_section(heading, terminator, (marker, scope_end), stop_prefixes=_PINNED_STOP_PREFIXES)
    if section.state is not State.FOUND or section.spans[0][0] != marker:
        return marker
    return section.spans[0][1]


def _indented_key(line) -> str | None:
    """The head an indented PROSE line becomes once its leading spaces and
    tabs go, or None when the line is a head already or would not be one."""
    key = line.content.strip(" \t")
    if line.kind is Kind.PROSE and not _is_head(line.content) and _is_head(key):
        return key
    return None


def reveal_candidates(doc: Document, located: Located) -> tuple[Counter, Counter]:
    """The lines of `doc` a reveal can turn into pins, as multisets of keys:
    (indented, below). `indented`: PROSE rows of the Pinned body `located`
    finds that are not heads but become one without their leading spaces and
    tabs (`_indented_key`). `below`: head rows below the pinned pair's end
    marker, as far as `clause_past_end_marker` can reach (up to a PACT section
    heading, a PACT marker line or the memory block's end), keyed by their
    content less trailing spaces and tabs. The keys are what
    `clause_unindented_heads` and `clause_past_end_marker` compare with
    `exclude`."""
    indented, below = Counter(), Counter()
    if located.state is State.FOUND:
        heading, last = located.spans[0]
        for row in range(heading + 1, last + 1):
            key = _indented_key(doc.lines[row])
            if key is not None:
                indented[key] += 1
    pair = _pinned_pair(doc)
    if pair is not None:
        marker, scope_end = pair
        reach = _below_marker(doc, marker, scope_end, past_stray_headings=True)
        for row in range(marker + 1, reach + 1):
            content = doc.lines[row].content
            if _is_head(content):
                below[content.rstrip(" \t")] += 1
    return indented, below


def clause_unindented_heads(t: Texts, R: tuple[int, int], exclude=frozenset()) -> int:
    """Heads of S that were already in R as indented lines: an unaligned PROSE
    head row of S pairs, one to one, with an unaligned PROSE row of R that is
    not a head but becomes this one without its leading spaces and tabs
    (`_indented_key`) and whose key is not in `exclude`. First by the same
    text; then a remaining head with a remaining row in the same gap of the
    alignment (the aligned rows on either side map to each other), in row
    order, which reads a heading un-indented and retitled in place. An
    unaligned head of R, fenced or not, and a remaining head of S with the
    same text are one heading moved, in row order, and neither counts
    below. Every other head of R that left a gap stands for a remaining head
    of S there first, as a rename, so a gap pairs in place at most its
    remaining heads less the heads it lost. Fenced rows never pair. Returns
    the number of pairs."""
    s, e = t.S
    t.budget.spend(R[1] - R[0] + 1 + e - s + 1)
    by_key: dict[str, list[int]] = {}
    for i in range(R[0], R[1] + 1):
        if i not in t.p2q:
            key = _indented_key(t.pre.lines[i])
            if key is not None and key not in exclude:
                by_key.setdefault(key, []).append(i)
    if not by_key:
        return 0
    heads = [j for j in range(s, e + 1)
             if j not in t.q2p and t.post.lines[j].kind is Kind.PROSE and _is_head(t.b[j])]
    pairs, used, left = 0, set(), []
    for j in heads:
        rows = by_key.get(t.b[j])
        if rows:
            used.add(rows.pop(0))
            pairs += 1
        else:
            left.append(j)
    remaining = sorted(i for rows in by_key.values() for i in rows if i not in used)
    if not left or not remaining:
        return pairs
    pre_aligned, post_aligned = sorted(t.p2q), sorted(t.q2p)
    t.budget.spend(len(pre_aligned) + len(post_aligned) + len(remaining) + len(left))

    def bounds(rows, k):
        at = bisect.bisect_left(rows, k)
        return (rows[at - 1] if at else None), (rows[at] if at < len(rows) else None)

    def post_gap(j):
        lo, hi = bounds(post_aligned, j)
        return (None if lo is None else t.q2p[lo]), (None if hi is None else t.q2p[hi])

    lost: dict[str, list[int]] = {}
    t.budget.spend(R[1] - R[0] + 1)
    for i in range(R[0], R[1] + 1):
        if i not in t.p2q and _is_head(t.a[i]):
            lost.setdefault(t.a[i], []).append(i)
    stay = []
    for j in left:
        rows = lost.get(t.b[j])
        if rows:
            rows.pop(0)
        else:
            stay.append(j)
    rows_in: Counter = Counter(bounds(pre_aligned, i) for i in remaining)
    room: Counter = Counter(post_gap(j) for j in stay)
    for rows in lost.values():
        for i in rows:
            room[bounds(pre_aligned, i)] -= 1
    for j in stay:
        gap = post_gap(j)
        if rows_in[gap] > 0 and room[gap] > 0:
            rows_in[gap] -= 1
            room[gap] -= 1
            pairs += 1
    return pairs


def clause_intact(t: Texts, R: tuple[int, int], i: int, j: int) -> bool:
    """Post CODE row j, aligned to pre row i in R, keeps its block: its fence
    opener and closer are aligned to pre rows on either side of i."""
    opener, closer = t.fence[j]
    po, pc = t.q2p.get(opener), t.q2p.get(closer)
    return po is not None and pc is not None and po < i < pc


def clause_refenced(t: Texts, i: int, j: int) -> bool:
    """Row j's whole fenced block equals the pre rows at the same offsets
    around pre row i, or only its fence lines changed (character, length or
    info string) and those two pre rows are fence-shaped."""
    opener, closer = t.fence[j]
    lo, hi = i - (j - opener), i + (closer - j)
    if lo < 0 or hi >= len(t.a):
        return False
    t.budget.spend(closer - opener + 1)
    if t.b[opener:closer + 1] == t.a[lo:hi + 1]:
        return True
    return (bool(_FENCE_SHAPE.match(t.pre.lines[lo].content))
            and bool(_FENCE_SHAPE.match(t.pre.lines[hi].content))
            and t.b[opener + 1:closer] == t.a[lo + 1:hi])


def _unaligned_fence_texts(t: Texts, R: tuple[int, int]) -> set:
    key = ("fences", R)
    if key not in t.cache:
        t.cache[key] = {t.a[k] for k in range(R[0], R[1] + 1)
                        if k not in t.p2q and _FENCE_SHAPE.match(t.pre.lines[k].content)}
    return t.cache[key]


def _unaligned_prose_texts_in_s(t: Texts) -> set:
    if "prose" not in t.cache:
        s, e = t.S
        t.cache["prose"] = {t.b[q] for q in range(s, e + 1)
                            if q not in t.q2p and t.post.lines[q].kind is Kind.PROSE}
    return t.cache["prose"]


def clause_guarded_pairing(t: Texts, R: tuple[int, int], j: int) -> bool:
    """Row j is aligned to a pre row of R that was CODE (the caller checks
    that). It stays one line, not new, when (a) both its fence lines already
    existed (aligned to a fence-shaped pre line, or unaligned with the text of
    an unaligned fence-shaped line of R) and (b) no unaligned prose line in S
    has its text, which would read as a hidden pin revealed."""
    def existed(row):
        if row in t.q2p:
            return bool(_FENCE_SHAPE.match(t.pre.lines[t.q2p[row]].content))
        return t.b[row] in _unaligned_fence_texts(t, R)

    opener, closer = t.fence[j]
    if not (existed(opener) and existed(closer)):
        return False
    return t.b[j] not in _unaligned_prose_texts_in_s(t)


def _run_gone(t: Texts, start: int, size: int) -> bool:
    """A pre run is no longer in place unless one of its `### ` lines is
    aligned, or one of its lines is aligned at the same offset inside an
    identical block after the change. A blank line, a shared command or a
    fence line paired elsewhere carries no identity."""
    run = t.a[start:start + size]
    for k in range(start, start + size):
        q = t.p2q.get(k)
        if q is None:
            continue
        if _is_head(t.pre.lines[k].content):
            return False
        b0 = q - (k - start)
        if 0 <= b0 and b0 + size <= len(t.b):
            t.budget.spend(size)
            if t.b[b0:b0 + size] == run:
                return False
    return True


def clause_moved_block(t: Texts, R: tuple[int, int], j: int, claims: Claims) -> bool:
    """Row j's block was already credited as moved, or the block, fence lines
    included, equals a run of R that is no longer in place, not used before
    and touching no used row. A credited block covers every `### ` line in it,
    and the run is used once. One budget step per line compared."""
    opener, closer = t.fence[j]
    if (opener, closer) in claims.blocks:
        return True
    block = t.b[opener:closer + 1]
    size = len(block)
    first = block[0]
    for start in range(R[0], R[1] - size + 2):
        if t.a[start] != first:
            t.budget.spend(1)
            continue
        t.budget.spend(size)
        if (t.a[start:start + size] != block or (start, size) in claims.runs
                or any(k in claims.rows for k in range(start, start + size))
                or not _run_gone(t, start, size)):
            continue
        claims.runs.add((start, size))
        claims.blocks.add((opener, closer))
        claims.rows.update(range(start, start + size))
        return True
    return False


def clause_edited_in_place(t: Texts, R: tuple[int, int], j: int, claims: Claims) -> bool:
    """Unaligned row j, whose fence opener and closer are aligned to two rows
    of R, pairs with one unaligned, unused `### ` line between those two: a
    line edited in place. The R rows between must stay inside the block the
    opener starts: a certain row must be CODE, and an uncertain row (past an
    unclosed fence) must not close that opener. So a snippet line pairs only
    with a snippet line, never with a line the text before reads as a pin."""
    opener, closer = t.fence[j]
    po, pc = t.q2p.get(opener), t.q2p.get(closer)
    if po is None or pc is None or not R[0] <= po < pc <= R[1]:
        return False
    t.budget.spend(pc - po)
    fence = _opener(t.pre.lines[po].content)
    between = range(po + 1, pc)
    for k in between:
        line = t.pre.lines[k]
        if line.kind is Kind.UNKNOWN:
            if fence is None or _closes(line.content, fence):
                return False
        elif line.kind is not Kind.CODE:
            return False
    for k in between:
        if _is_head(t.pre.lines[k].content) and k not in t.p2q and k not in claims.rows:
            claims.rows.add(k)
            return True
    return False


def clause_no_leaving_credit(t: Texts, R: tuple[int, int]) -> int:
    """A `### ` line that leaves a fenced block of R (deleted, moved out of
    the block or the section, or unfenced) earns no credit: it may be a pin a
    stray fence hid, and removing a pin must free its slot."""
    return 0


def fenced_line_is_new(t: Texts, R: tuple[int, int], j: int, claims: Claims) -> bool:
    """A fenced `### ` row j of S is new unless a clause pairs it with R."""
    i = t.q2p.get(j)
    if i is not None and R[0] <= i <= R[1]:
        if clause_intact(t, R, i, j) or clause_refenced(t, i, j):
            return False
        if t.pre.lines[i].kind is Kind.CODE and clause_guarded_pairing(t, R, j):
            return False
    if clause_moved_block(t, R, j, claims):
        return False
    return not (i is None and clause_edited_in_place(t, R, j, claims))


def pin_growth(before: Document, after: Document, *, budget: int | None = None,
               trim: bool = True, detail: dict | None = None, exclude=None) -> int | None:
    """How many pins the change from `before` to `after` added to the Pinned
    section; zero or less when it added none. None when the Pinned section
    after the change is not FOUND. Raises SizeBound past `budget` steps
    (STEP_BUDGET, read at call time, when None). A `detail` dict receives the
    call's `Texts` and R, which `pin_cap_decision`'s size axis reads.
    `exclude` (indented, below), sets of `reveal_candidates` keys, names the
    lines whose reveal counts as new; None excludes none."""
    located = locate_pinned(after)
    if located.state is not State.FOUND:
        return None
    heading, last = located.spans[0]
    S = (heading + 1, last)
    a = [line.content.rstrip(" \t") for line in before.lines]
    b = [line.content.rstrip(" \t") for line in after.lines]
    steps = _Budget(STEP_BUDGET if budget is None else budget)
    steps.spend(len(a) + len(b))  # one step per row for the row passes over both texts
    p2q, q2p = _align(a, b, steps, trim, _memory_range(before, after))
    t = Texts(before, after, a, b, p2q, q2p, S, steps, _fence_pairs(after), {})
    indented, below = (frozenset(), frozenset()) if exclude is None else (exclude.indented, exclude.below)
    previous = locate_pinned(before)
    withheld = 0
    if previous.state is State.FOUND:
        R = clause_past_stray_heading(t, (previous.spans[0][0] + 1, previous.spans[0][1]))
        R, withheld = clause_past_end_marker(t, R, below)
    else:
        R = clause_region_r(t)
    raw_after = sum(_is_head(after.lines[j].content) for j in range(S[0], S[1] + 1))
    raw_before = sum(_is_head(before.lines[i].content) for i in range(R[0], R[1] + 1))
    if detail is not None:
        detail.update(texts=t, region=R)
    claims = Claims(set(), set(), set())
    new_fenced = sum(fenced_line_is_new(t, R, j, claims) for j in range(S[0], S[1] + 1)
                     if _is_head(after.lines[j].content) and after.lines[j].kind is not Kind.PROSE)
    return (raw_after - raw_before + withheld - new_fenced - clause_unindented_heads(t, R, indented)
            + clause_no_leaving_credit(t, R))


def run_with_timer(work, use_timer: bool = True):
    """Run `work()` under two interrupting timers, either raising SizeBound:
    TIMER_SECONDS of this process's own processor time (SIGPROF), which a busy
    machine cannot use up for it, and WALL_SECONDS of wall time (SIGALRM), which
    ends a wait that uses no processor time. No timers where `signal.setitimer`
    is missing or off the main thread, where a handler cannot be set: decide
    without them, never refuse because of them. Both timers are disarmed and
    the previous handlers restored."""
    if not use_timer or not hasattr(signal, "setitimer"):
        return work()

    def stop(note):
        # Disarm both timers before raising: a stop that lands as the work ends
        # can skip the restore below, and must leave no timer armed.
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.setitimer(signal.ITIMER_PROF, 0)
        raise SizeBound(note)

    def on_processor_time(signum, frame):
        stop(f"the pin cap check used more than {TIMER_SECONDS} s of processor time")

    def on_wall_time(signum, frame):
        stop(f"the pin cap check ran for more than {WALL_SECONDS} s")

    try:
        previous_processor = signal.signal(signal.SIGPROF, on_processor_time)
    except ValueError:
        return work()
    previous_wall = signal.signal(signal.SIGALRM, on_wall_time)
    try:
        signal.setitimer(signal.ITIMER_PROF, TIMER_SECONDS)
        signal.setitimer(signal.ITIMER_REAL, WALL_SECONDS)
        return work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL if previous_wall is None else previous_wall)
        signal.signal(signal.SIGPROF, signal.SIG_DFL if previous_processor is None else previous_processor)


class PinDecision(NamedTuple):
    verdict: str  # "ALLOW", "ALLOW_ADVISORY" or "DENY"
    pins_before: int
    pins_after: int
    growth: int | None  # None when the rule did not run
    # None for a plain verdict. Otherwise this decision's "not_found",
    # "size_bound", "error", "count" or "size", or the pin-cap gate's own
    # "override" or "unreadable". On "size_bound" the check stopped before it
    # could count, so pins_before and pins_after are 0.
    cause: str | None
    reason: str | None  # the advisory or the deny text
    # On a count denial, the size cap's own deny text when the change crosses it
    # too; None otherwise. A report names both caps from it.
    size_reason: str | None = None


def stopped_early(bound: SizeBound) -> PinDecision:
    """The verdict when the pin cap check passes its step budget or timer: the
    change is allowed, with a note naming the bound."""
    return PinDecision("ALLOW_ADVISORY", 0, 0, None, "size_bound",
                       f"The pin cap check stopped early and allowed this change: {bound}.")


def pin_spans(doc: Document, S: tuple[int, int]) -> list[tuple[int, int]]:
    """Each pin of the Pinned body S of `doc` as the rows it covers, from its
    comment row (or heading) to the row before the next pin's, in the order
    `section_pins` reads them. The pin-cap gate reads overrides through it."""
    from pin_caps import _date_comment_row

    s, e = S
    headings = [j for j in range(s, e + 1)
                if doc.lines[j].kind is Kind.PROSE and _is_head(doc.lines[j].content)]
    starts = []
    for index, heading in enumerate(headings):
        floor = headings[index - 1] + 1 if index else s
        comment = _date_comment_row(doc, floor, heading)
        starts.append(heading if comment is None else comment)
    return [(start, (starts[k + 1] - 1) if k + 1 < len(starts) else e) for k, start in enumerate(starts)]


def _size_pins(t: Texts, R: tuple[int, int], post_pins: list[Pin]) -> tuple[list[Pin], list[Pin]]:
    """The size axis's pins when the text before has no located Pinned section.

    R is cut into stretches, one per pin of S with an aligned row: from the row
    after the last R row aligned into any earlier pin, through the row before
    the first R row aligned into any later pin (at least through the last R row
    aligned into this one). So the rows a pin lost on either side, a trimmed
    end included, stay in its stretch; neighbouring stretches may overlap,
    which only ever raises the size before. Rows of R past every stretch form
    the tail. Each stretch and the tail is one pseudo-pin charged as a body is,
    so an unchanged or trimmed pin is compared with its own rows before.

    A pseudo-pin carries no override: a stretch can merge a deleted overridden
    pin with a neighbour, and the override would hide the neighbour's size
    before. Pins of S with no aligned row are left out after. Signed off by
    the user: with no located section before, a brand-new oversize pin, or an
    oversize pin whose override is removed, is not refused on size. Blank rows
    carry no identity, so only non-blank aligned rows count.
    """
    kept = []
    for span, pin in zip(pin_spans(t.post, t.S), post_pins):
        t.budget.spend(span[1] - span[0] + 1)
        aligned = [t.q2p[j] for j in range(span[0], span[1] + 1)
                   if j in t.q2p and R[0] <= t.q2p[j] <= R[1] and t.b[j].strip()]
        if aligned:
            kept.append((pin, min(aligned), max(aligned)))
    # later[index]: the first R row of every pin after `index`, at its least
    later: list[int | None] = [None] * len(kept)
    least: int | None = None
    for index in range(len(kept) - 1, -1, -1):
        later[index] = least
        first = kept[index][1]
        least = first if least is None else min(least, first)
    stretches, top = [], R[0] - 1
    for index, (_, _, last) in enumerate(kept):
        bound = later[index]
        upper = max(last, (bound if bound is not None else R[1] + 1) - 1)
        stretches.append((top + 1, upper))
        top = max(top, last)
    if stretches and max(upper for _, upper in stretches) < R[1]:
        stretches.append((max(upper for _, upper in stretches) + 1, R[1]))
    elif not stretches:
        stretches.append(R)
    before = [Pin(heading="", body="", body_chars=_charge(t.pre, first, last), date_comment=None,
                  override_rationale=None, is_stale=False) for first, last in stretches if first <= last]
    return before, [pin for pin, _, _ in kept]


def _not_found_text(located: Located) -> str:
    detail = f": {located.reason}" if located.reason else ""
    return ("PACT could not locate the Pinned section of this CLAUDE.md, so its pin cap was not "
            f"checked ({located.state.value}{detail}).")


def pin_cap_decision(before: str, after: str, *, use_timer: bool = True, trim: bool = True,
                     revoked: tuple = ((), ()), credit=None, detail: dict | None = None) -> PinDecision:
    """The pin cap's verdict on a change from `before` to `after` (whole texts;
    `before` is "" when no file resolved). Never raises: the size check, a step
    budget or the timer, which covers the whole decision, allows with the size
    advisory, and any other failure allows with an advisory, cause "error".

    `revoked` holds the indices, among the pins of the Pinned section before
    and after, of pins whose size override counts as none. `credit` (count,
    removed, exclude) is what this agent's earlier allowed changes along one
    chain removed and added: `count` pins it may add past the cap, `removed`
    the pins it took out (`Pin`s), `exclude` the `reveal_candidates` keys of
    lines it put in (indented, below). None gives no credit and no exclusion.
    A `detail` dict, once the decision has read both sections, receives
    "before_doc", "after_doc", "before_located", "after_located",
    "pins_before" and "pins_after" (None for a side not located), "growth"
    (the rule's growth before credit, measured whenever both sections are
    located even where the decision did not need it; None when the rule could
    not measure it), and with the rule, "region" and "texts". Measuring growth
    for `detail` alone never changes the decision."""
    try:
        return run_with_timer(lambda: _decide(before, after, trim, revoked, credit, detail), use_timer)
    except SizeBound as bound:
        return stopped_early(bound)
    except Exception as error:  # an over-block is the worst outcome, so a failure allows
        return PinDecision("ALLOW_ADVISORY", 0, 0, None, "error",
                           f"PACT could not check the pin cap: {type(error).__name__}")


def _revoke(pins: list[Pin], indices) -> list[Pin]:
    """`pins` with no size override on the pins at `indices`."""
    return [pin._replace(override_rationale=None) if index in indices else pin for index, pin in enumerate(pins)]


def _live_removed(removed, pins_before: list[Pin], budget: _Budget | None) -> list[Pin]:
    """The removed pins that no pin before continues (`pin_caps.continues`):
    a pin back in the section already stands for its removal, so one removal
    is never spent twice. One step of `budget` per character the comparisons
    read."""
    spend = budget.spend if budget is not None else (lambda steps: None)
    live = []
    for pin in removed:
        for before_pin in pins_before:
            spend(1 + pin.body_chars + before_pin.body_chars)
            if continues(pin, before_pin):
                break
        else:
            live.append(pin)
    return live


def _decide(before: str, after: str, trim: bool, revoked: tuple = ((), ()), credit=None,
            detail: dict | None = None) -> PinDecision:
    from staleness import locate_pinned as locate_any_pinned

    check_size(before, after)
    after_doc = parse(after)
    located = locate_pinned(after_doc)
    if located.state is not State.FOUND:
        # No Pinned section anywhere after the change: nothing to cap, and
        # nothing PACT failed to read.
        if located.state is State.ABSENT and locate_any_pinned(after_doc).state is State.ABSENT:
            return PinDecision("ALLOW", 0, 0, None, None, None)
        return PinDecision("ALLOW_ADVISORY", 0, 0, None, "not_found", _not_found_text(located))
    before_doc = parse(before)
    post_pins = _revoke(section_pins(after_doc, located), revoked[1])
    previous = locate_pinned(before_doc)
    found_before = previous.state is State.FOUND
    exclude = None if credit is None else credit.exclude
    growth, rule = None, {}
    if len(post_pins) > PIN_COUNT_CAP or not found_before:
        growth = pin_growth(before_doc, after_doc, trim=trim, detail=rule, exclude=exclude)
    pre_pins = None
    if found_before:
        pre_pins = _revoke(section_pins(before_doc, previous), revoked[0])
        size_before, size_after = pre_pins, post_pins
        pins_before = len(pre_pins) if growth is None else len(post_pins) - growth
    else:
        t, R = rule["texts"], rule["region"]
        if R[0] > R[1]:  # nothing before to compare with: a first Write
            size_before, size_after = [], post_pins
        else:
            size_before, size_after = _size_pins(t, R, post_pins)
        pins_before = len(post_pins) - (growth or 0)
    # Both size_violation calls spend from one budget.
    size_budget = _Budget(part=SIZE_RULE) if found_before else None
    if found_before:
        # Each new or grown pin's size on its own, renames and moves kept as
        # the same pin.
        size_reason = size_violation(size_before, size_after, size_budget)
    else:
        size_reason = compute_deny_reason(size_before, size_after, growth=0)
    if size_reason is not None and found_before:
        # A pin revealed below a fixed stray heading was there before on the
        # size axis too: read the pins before over R as the rule extends it,
        # and allow when either reading allows. The rule is not monotone in
        # the pins before, so a wider reading alone could refuse.
        if growth is None:
            growth = pin_growth(before_doc, after_doc, trim=trim, detail=rule, exclude=exclude)
            pins_before = len(post_pins) - (growth or 0)
        if size_violation(pins_in_rows(before_doc, *rule["region"]), size_after, size_budget) is None:
            size_reason = None
    if size_reason is not None and found_before and credit is not None and credit.removed:
        # A pin this agent removed earlier in the chain was there before on
        # the size axis too: read it among the pins before, and allow when
        # that reading allows. When it refuses as well, its reason names the
        # pin as grown against its removed self rather than as new.
        live = _live_removed(credit.removed, size_before, size_budget)
        if live:
            size_reason = size_violation(list(size_before) + live, size_after, size_budget)
    measured = growth
    growth = growth or 0
    demote = growth - (0 if credit is None else credit.count)
    if growth > 0 and len(post_pins) > PIN_COUNT_CAP and demote > 0:
        reason = compute_deny_reason(size_before, post_pins, growth=growth, demote=demote)
        cause = "count"
    else:
        reason, size_reason = size_reason, None
        cause = "size"
    if detail is not None:
        if measured is None and found_before:
            # The credit record reads the rule's growth on every allowed change, so it is
            # measured here, after the verdict, for `detail` alone: a stop or a failure
            # inside it leaves the verdict as it is and the growth unknown.
            try:
                measured = pin_growth(before_doc, after_doc, trim=trim, detail=rule, exclude=exclude)
            except Exception:  # noqa: BLE001 — a measure for `detail` alone never changes the decision
                measured = None
        detail.update(before_doc=before_doc, after_doc=after_doc, before_located=previous, after_located=located,
                      pins_before=pre_pins, pins_after=post_pins, growth=measured)
        if rule:
            detail.update(region=rule["region"], texts=rule["texts"])
    if reason is None:
        return PinDecision("ALLOW", pins_before, len(post_pins), growth, None, None)
    return PinDecision("DENY", pins_before, len(post_pins), growth, cause, reason, size_reason)
