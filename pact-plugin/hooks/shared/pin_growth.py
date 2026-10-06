#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/pin_growth.py

Summary: The pin-growth rule. Two CLAUDE.md texts in, how many pins the
change ADDED to the Pinned section out. Pure: no I/O. The pin-cap gate
refuses a change only when it adds pins past the cap, so a rename, a move, a
fenced snippet that holds `### ` lines, or a reveal of a pin hidden by a stray
fence is never counted as growth.

Used by: pin_caps_gate (lazily, on the CLAUDE.md path only) and
claude_md_drift (lazily, when a file's hash changed), both through
`pin_cap_decision`. Nothing else imports it, so the parser's other importers
never load `difflib`.

THE RULE. S is the Pinned body after the change, from the one Pinned locator.
R is the matching region before: the Pinned body when the text before has one,
otherwise `clause_region_r`. The two texts are aligned line by line, once, over
the memory block only (the whole text when either side's block is not
FOUND), with shared leading and trailing lines trimmed first.

    growth = (`### ` lines in S) - (`### ` lines in R)      both fence-blind
             - (fenced `### ` lines in S that no clause pairs with R)
             + clause_no_leaving_credit

A fenced `### ` line in S is not new when a clause pairs it with R: it is
intact in place, re-fenced, a guarded aligned pairing, part of a moved block,
or edited in place. A `### ` line that LEAVES a fenced block earns no credit,
because it may be a pin hidden by a stray fence, and removing a pin must free
its slot.

Each clause is a module-level function, called by its bare name at call time,
so a mutation test can replace one by monkeypatching this module.

THE BOUND. The alignment runs through `CountedMatcher`, which counts one step
per outer row and per inner step of `find_longest_match`; the moved-block and
edited-in-place searches count one step per line they compare. Past
`STEP_BUDGET` steps the rule raises `SizeBound`, and the caller allows with the
size advisory. A bound only ever allows.

THE DECISION. `pin_cap_decision` is the one verdict for the gate and the Bash
report: the Pinned section after the change not located allows with an
advisory; growth above zero with more than the cap's pins after it denies on
count; the size axis compares the pins before with the pins after; the budget,
the timer and any failure allow with an advisory. It never raises.
"""

from __future__ import annotations

import difflib
import re
import signal
from typing import NamedTuple

from pin_caps import PIN_COUNT_CAP, Pin, _charge, compute_deny_reason, section_pins

from .claude_md_manager import MEMORY_END_MARKER, MEMORY_START_MARKER
from .claude_md_markers import Document, Kind, Located, State, _closes, _opener, parse

STEP_BUDGET = 70_000_000
TIMER_SECONDS = 30

PACT_SECTIONS = ("## Working Memory", "## Retrieved Context")
_PINNED_HEADING_TEXT = "## Pinned Context"
_FENCE_SHAPE = re.compile(r" {0,3}(`{3,}|~{3,})")


class SizeBound(Exception):
    """The rule ran past its step budget, or the timer fired."""


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
    __slots__ = ("limit", "spent")

    def __init__(self, limit: int):
        self.limit = limit
        self.spent = 0

    def spend(self, steps: int) -> None:
        self.spent += steps
        if self.spent > self.limit:
            raise SizeBound(f"the pin-growth rule passed its step budget of {self.limit}")


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
               trim: bool = True, detail: dict | None = None) -> int | None:
    """How many pins the change from `before` to `after` added to the Pinned
    section; zero or less when it added none. None when the Pinned section
    after the change is not FOUND. Raises SizeBound past `budget` steps
    (STEP_BUDGET, read at call time, when None). A `detail` dict receives the
    call's `Texts` and R, which `pin_cap_decision`'s size axis reads."""
    located = locate_pinned(after)
    if located.state is not State.FOUND:
        return None
    heading, last = located.spans[0]
    S = (heading + 1, last)
    a = [line.content.rstrip(" \t") for line in before.lines]
    b = [line.content.rstrip(" \t") for line in after.lines]
    steps = _Budget(STEP_BUDGET if budget is None else budget)
    p2q, q2p = _align(a, b, steps, trim, _memory_range(before, after))
    t = Texts(before, after, a, b, p2q, q2p, S, steps, _fence_pairs(after), {})
    previous = locate_pinned(before)
    if previous.state is State.FOUND:
        R = (previous.spans[0][0] + 1, previous.spans[0][1])
    else:
        R = clause_region_r(t)
    raw_after = sum(_is_head(after.lines[j].content) for j in range(S[0], S[1] + 1))
    raw_before = sum(_is_head(before.lines[i].content) for i in range(R[0], R[1] + 1))
    if detail is not None:
        detail.update(texts=t, region=R)
    claims = Claims(set(), set(), set())
    new_fenced = sum(fenced_line_is_new(t, R, j, claims) for j in range(S[0], S[1] + 1)
                     if _is_head(after.lines[j].content) and after.lines[j].kind is not Kind.PROSE)
    return raw_after - raw_before - new_fenced + clause_no_leaving_credit(t, R)


def run_with_timer(work, use_timer: bool = True):
    """Run `work()` under an interrupting timer that raises SizeBound after
    TIMER_SECONDS. No timer where `signal.setitimer` is missing or off the main
    thread, where a handler cannot be set: decide without it, never refuse
    because of it. The previous handler is restored and the timer disarmed."""
    if not use_timer or not hasattr(signal, "setitimer"):
        return work()

    def on_alarm(signum, frame):
        raise SizeBound(f"the pin cap check ran past {TIMER_SECONDS} s")

    try:
        previous = signal.signal(signal.SIGALRM, on_alarm)
    except ValueError:
        return work()
    try:
        signal.setitimer(signal.ITIMER_REAL, TIMER_SECONDS)
        return work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL if previous is None else previous)


class PinDecision(NamedTuple):
    verdict: str  # "ALLOW", "ALLOW_ADVISORY" or "DENY"
    pins_before: int
    pins_after: int
    growth: int | None  # None when the rule did not run
    # None, "not_found", "size_bound", "error", "count" or "size". On
    # "size_bound" the check stopped before it could count, so pins_before and
    # pins_after are 0.
    cause: str | None
    reason: str | None  # the advisory or the deny text
    # On a count denial, the size cap's own deny text when the change crosses it
    # too; None otherwise. A report names both caps from it.
    size_reason: str | None = None


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
        aligned = [t.q2p[j] for j in range(span[0], span[1] + 1)
                   if j in t.q2p and R[0] <= t.q2p[j] <= R[1] and t.b[j].strip()]
        if aligned:
            kept.append((pin, min(aligned), max(aligned)))
    stretches, top = [], R[0] - 1
    for index, (_, _, last) in enumerate(kept):
        later = [first for _, first, _ in kept[index + 1:]]
        upper = max(last, (min(later) if later else R[1] + 1) - 1)
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


def pin_cap_decision(before: str, after: str, *, use_timer: bool = True, trim: bool = True) -> PinDecision:
    """The pin cap's verdict on a change from `before` to `after` (whole texts;
    `before` is "" when no file resolved). Never raises: the step budget or the
    timer, which covers the whole decision, allows with the size advisory, and
    any other failure allows with an advisory, cause "error"."""
    try:
        return run_with_timer(lambda: _decide(before, after, trim), use_timer)
    except SizeBound as bound:
        return PinDecision("ALLOW_ADVISORY", 0, 0, None, "size_bound",
                           f"The pin cap check stopped early and allowed this change: {bound}.")
    except Exception as error:  # an over-block is the worst outcome, so a failure allows
        return PinDecision("ALLOW_ADVISORY", 0, 0, None, "error",
                           f"PACT could not check the pin cap: {type(error).__name__}: {error}")


def _decide(before: str, after: str, trim: bool) -> PinDecision:
    after_doc = parse(after)
    located = locate_pinned(after_doc)
    if located.state is not State.FOUND:
        return PinDecision("ALLOW_ADVISORY", 0, 0, None, "not_found", _not_found_text(located))
    before_doc = parse(before)
    post_pins = section_pins(after_doc, located)
    previous = locate_pinned(before_doc)
    found_before = previous.state is State.FOUND
    growth, detail = None, {}
    if len(post_pins) > PIN_COUNT_CAP or not found_before:
        growth = pin_growth(before_doc, after_doc, trim=trim, detail=detail)
    if found_before:
        pre_pins = section_pins(before_doc, previous)
        size_before, size_after = pre_pins, post_pins
        pins_before = len(pre_pins) if growth is None else len(post_pins) - growth
    else:
        t, R = detail["texts"], detail["region"]
        if R[0] > R[1]:  # nothing before to compare with: a first Write
            size_before, size_after = [], post_pins
        else:
            size_before, size_after = _size_pins(t, R, post_pins)
        pins_before = len(post_pins) - (growth or 0)
    growth = growth or 0
    size_reason = None
    if growth > 0 and len(post_pins) > PIN_COUNT_CAP:
        reason = compute_deny_reason(size_before, post_pins, growth=growth)
        cause = "count"
        size_reason = compute_deny_reason(size_before, size_after, growth=0)
    else:
        reason = compute_deny_reason(size_before, size_after, growth=0)
        cause = "size"
    if reason is None:
        return PinDecision("ALLOW", pins_before, len(post_pins), growth, None, None)
    return PinDecision("DENY", pins_before, len(post_pins), growth, cause, reason, size_reason)
