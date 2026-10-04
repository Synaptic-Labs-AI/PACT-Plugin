"""Replacements for the pin-growth rule's clause functions, one per mutant.

Each replacement takes the same arguments as the function it replaces in
hooks/shared/pin_growth.py and changes one thing. The bases (every option off)
are written from the rule's specification and the scratch model the rule was
designed on, not from pin_growth.py; test_pin_growth_mutants checks that each base
decides exactly as the function it stands in for, so a killed mutant is killed by
its one change.
"""

import collections
import re

import claude_md_fence_oracle as oracle

from shared.claude_md_markers import Kind, State

FENCE_SHAPE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
PINNED_HEADING = "## Pinned Context"
MEMORY_START = "<!-- PACT_MEMORY_START -->"
MEMORY_END = "<!-- PACT_MEMORY_END -->"
PACT_SECTIONS = ("## Working Memory", "## Retrieved Context")


def _is_head(content):
    return content.startswith("### ")


def region(*, read_hidden=False, whole=False, first_end=False, fenced_literals=False,
           no_section_fallback=False, any_section_fallback=False, first_moved=False, no_moved=False,
           no_ceiling=False, no_floor=False, floor_only=False):
    """clause_region_r(t): R when the Pinned section before the change is not located.
    It starts after the first `## Pinned Context` below the first memory-start line, or
    after the first one in the file when none lies below it; the last memory-end line
    after that start is a ceiling for the first two end searches."""
    def clause_region_r(t):
        if whole:
            return 0, len(t.a) - 1
        s, e = t.S
        starts = [l.row for l in t.pre.lines
                  if (read_hidden or not l.in_html) and l.content.strip() == PINNED_HEADING]
        if not starts:
            return 0, -1
        memory_start = next((l.row for l in t.pre.lines if l.content.strip() == MEMORY_START), None)
        floored = [r for r in starts if memory_start is not None and r > memory_start]
        if no_floor:
            floored = []
        if floor_only and not floored:
            return 0, -1
        lo = (floored or starts)[0] + 1
        term = t.b[e + 1] if e + 1 < len(t.b) else None
        new_prose = {t.b[j] for j in range(len(t.b))
                     if j not in t.q2p and t.post.lines[j].kind is Kind.PROSE and not s <= j <= e}

        def literal(i):
            j = t.p2q.get(i)
            return j is not None and t.post.lines[j].kind is not Kind.PROSE

        def real_section(i):
            j = t.p2q.get(i)
            if j is not None:
                return t.post.lines[j].kind is Kind.PROSE and not s <= j <= e
            return t.a[i] in new_prose

        memory_ends = [i for i in range(lo, len(t.a)) if t.pre.lines[i].content.strip() == MEMORY_END]
        limit = memory_ends[-1] + 1 if memory_ends and not no_ceiling else len(t.a)
        ends = [i for i in range(lo, limit)
                if term is not None and t.a[i] == term and (fenced_literals or not literal(i))]
        if not ends and not no_section_fallback:
            ends = [i for i in range(lo, limit)
                    if t.a[i] in PACT_SECTIONS and (any_section_fallback or real_section(i))]
        if not ends:
            ends = memory_ends[-1:]
        hi = ((ends[0] if first_end else ends[-1]) - 1) if ends else len(t.a) - 1
        if not no_moved:
            moved_to = {t.b[j] for j in range(len(t.b))
                        if j not in t.q2p and t.post.lines[j].kind is Kind.PROSE
                        and not s <= j <= e and t.post.lines[j].content.startswith("## ")}
            cands = [i for i in range(lo, hi + 1)
                     if i not in t.p2q and t.a[i] in PACT_SECTIONS and t.a[i] in moved_to]
            if cands:
                return lo, (cands[0] if first_moved else cands[-1]) - 1
        return lo, hi
    return clause_region_r


def intact(*, always=None):
    """clause_intact(t, R, i, j): the fence partners of aligned fenced row j enclose i."""
    def clause_intact(t, R, i, j):
        if always is not None:
            return always
        o, c = t.fence[j]
        po, pc = t.q2p.get(o), t.q2p.get(c)
        return po is not None and pc is not None and po < i < pc
    return clause_intact


def refenced(*, off=False, identical_only=False):
    """clause_refenced(t, i, j): the same block at the same offsets, or re-fenced."""
    def clause_refenced(t, i, j):
        if off:
            return False
        o, c = t.fence[j]
        lo, hi = i - (j - o), i + (c - j)
        if lo < 0 or hi >= len(t.a):
            return False
        if t.b[o:c + 1] == t.a[lo:hi + 1]:
            return True
        if identical_only:
            return False
        return (bool(FENCE_SHAPE.match(t.pre.lines[lo].content)) and bool(FENCE_SHAPE.match(t.pre.lines[hi].content))
                and t.b[o + 1:c] == t.a[lo + 1:hi])
    return clause_refenced


def guarded(*, always=None, no_fence_clause=False, no_twin_clause=False):
    """clause_guarded_pairing(t, R, j): an aligned fenced row whose pre row was code."""
    def clause_guarded_pairing(t, R, j):
        if always is not None:
            return always

        def existed(f):
            if f in t.q2p:
                return bool(FENCE_SHAPE.match(t.pre.lines[t.q2p[f]].content))
            return any(k not in t.p2q and FENCE_SHAPE.match(t.pre.lines[k].content) and t.a[k] == t.b[f]
                       for k in range(R[0], R[1] + 1))

        o, c = t.fence[j]
        if not no_fence_clause and not (existed(o) and existed(c)):
            return False
        s, e = t.S
        if not no_twin_clause and any(t.post.lines[q].kind is Kind.PROSE and q not in t.q2p and t.b[q] == t.b[j]
                                      for q in range(s, e + 1)):
            return False
        return True
    return clause_guarded_pairing


def moved(*, off=False, first_heading_only=False, inner_rows_only=False, ignore_aligned_heading=False,
          fences_unaligned=False):
    """clause_moved_block(t, R, j, claims): the block equals a run of R no longer in place."""
    def gone(t, st, n):
        if fences_unaligned:
            return all(k not in t.p2q for k in range(st, st + n))
        if inner_rows_only:
            return not any(k in t.p2q for k in range(st + 1, st + n - 1))
        run = t.a[st:st + n]
        for k in range(st, st + n):
            q = t.p2q.get(k)
            if q is None:
                continue
            if _is_head(t.pre.lines[k].content) and not ignore_aligned_heading:
                return False
            b0 = q - (k - st)
            if 0 <= b0 and b0 + n <= len(t.b) and t.b[b0:b0 + n] == run:
                return False
        return True

    def clause_moved_block(t, R, j, claims):
        if off:
            return False
        o, c = t.fence[j]
        if not first_heading_only and (o, c) in claims.blocks:
            return True
        block = t.b[o:c + 1]
        n = len(block)
        for st in range(R[0], R[1] - n + 2):
            if (st, n) in claims.runs or any(k in claims.rows for k in range(st, st + n)):
                continue
            if t.a[st:st + n] == block and gone(t, st, n):
                claims.runs.add((st, n))
                claims.blocks.add((o, c))
                claims.rows.update(range(st, st + n))
                return True
        return False
    return clause_moved_block


def edited_in_place(*, off=False, unknown_by_shape=False, certain_only=False, no_code_requirement=False,
                    any_span=False):
    """clause_edited_in_place(t, R, j, claims): an unaligned fenced row edited in place.
    Every row between the block's two aligned fence lines must be, before the change,
    a certain code row, or an uncertain row that cannot close the opener's fence; the
    renamed line pairs with an unused ### line there."""
    def qualifies(t, po, pc):
        fence = oracle._opener(t.pre.lines[po].content)
        for k in range(po + 1, pc):
            line = t.pre.lines[k]
            if line.kind is Kind.UNKNOWN:
                if certain_only:
                    return False
                if unknown_by_shape:
                    if FENCE_SHAPE.match(line.content):
                        return False
                elif fence is None or oracle._closes(line.content, *fence):
                    return False
            elif line.kind is not Kind.CODE and not no_code_requirement:
                return False
        return True

    def clause_edited_in_place(t, R, j, claims):
        if off:
            return False
        o, c = t.fence[j]
        po, pc = t.q2p.get(o), t.q2p.get(c)
        if po is None or pc is None or not R[0] <= po < pc <= R[1]:
            return False
        if not any_span and not qualifies(t, po, pc):
            return False
        for k in range(po + 1, pc):
            if _is_head(t.pre.lines[k].content) and k not in t.p2q and k not in claims.rows:
                claims.rows.add(k)
                return True
        return False
    return clause_edited_in_place


def _is_opener(doc, r):
    n, k = 0, r - 1
    while k >= 0 and doc.lines[k].kind in (Kind.FENCE, Kind.CODE):
        n += doc.lines[k].kind is Kind.FENCE
        k -= 1
    return n % 2 == 0


def _fence_pair(doc, i):
    o = i
    while o >= 0 and doc.lines[o].kind is not Kind.FENCE:
        o -= 1
    c = i
    while c < len(doc.lines) and doc.lines[c].kind is not Kind.FENCE:
        c += 1
    return o, c


def leaving_credit(locate, *, kept=False, opener_check=True, departure=False):
    """clause_no_leaving_credit(t, R): 0. kept credits a ### line deleted from a fenced
    block that stays; departure credits a fenced block that leaves the section.
    `locate` is the rule's Pinned locator, used to tell the two R cases apart."""
    def kept_rows(t, R, pre_found):
        s, e = t.S
        out = []
        for i in range(R[0], R[1] + 1):
            line = t.pre.lines[i]
            if not _is_head(line.content) or i in t.p2q:
                continue
            if pre_found:
                if line.kind is not Kind.CODE:
                    continue
                o, c = _fence_pair(t.pre, i)
            else:
                o = i - 1
                while o >= 0 and not FENCE_SHAPE.match(t.pre.lines[o].content):
                    o -= 1
                c = i + 1
                while c < len(t.pre.lines) and not FENCE_SHAPE.match(t.pre.lines[c].content):
                    c += 1
                if o < 0:
                    continue
            qo, qc = t.p2q.get(o), t.p2q.get(c)
            if (qo is not None and qc is not None and s <= qo < qc <= e
                    and t.post.lines[qo].kind is Kind.FENCE and t.post.lines[qc].kind is Kind.FENCE
                    and all(t.post.lines[k].kind is Kind.CODE for k in range(qo + 1, qc))
                    and (not opener_check or _is_opener(t.post, qo))):
                out.append(i)
        return out

    def same_block_at(t, i, j):
        o, c = _fence_pair(t.post, j)
        if o < 0 or c >= len(t.post.lines):
            return False
        lo, hi = i - (j - o), i + (c - j)
        if lo < 0 or hi >= len(t.a):
            return False
        if t.b[o:c + 1] == t.a[lo:hi + 1]:
            return True
        return (bool(FENCE_SHAPE.match(t.pre.lines[lo].content)) and bool(FENCE_SHAPE.match(t.pre.lines[hi].content))
                and t.b[o + 1:c] == t.a[lo + 1:hi])

    def clause_no_leaving_credit(t, R):
        credit = 0
        s, e = t.S
        if kept:
            pre_found = locate(t.pre).state is State.FOUND
            new_prose = collections.Counter(t.b[j] for j in range(s, e + 1)
                                            if _is_head(t.post.lines[j].content)
                                            and t.post.lines[j].kind is Kind.PROSE and j not in t.q2p)
            for i in kept_rows(t, R, pre_found):
                if new_prose[t.a[i]] > 0:
                    new_prose[t.a[i]] -= 1
                else:
                    credit += 1
        if departure:
            for i in range(R[0], R[1] + 1):
                if not _is_head(t.pre.lines[i].content):
                    continue
                j = t.p2q.get(i)
                if (j is not None and not s <= j <= e and t.post.lines[j].kind is Kind.CODE
                        and same_block_at(t, i, j)):
                    credit += 1
        return credit
    return clause_no_leaving_credit


def no_new_fenced_lines():
    """fenced_line_is_new(t, R, j, claims) -> False: every fenced ### line counts as a pin."""
    def fenced_line_is_new(t, R, j, claims):
        return False
    return fenced_line_is_new


def net_growth(original, locate):
    """pin_growth(before, after, ...): the old rule for a located section before the change,
    growth only when both the fence-blind and the fence-aware counts grow."""
    def pin_growth(before, after, **kwargs):
        prev, loc = locate(before), locate(after)
        if loc.state is not State.FOUND or prev.state is not State.FOUND:
            return original(before, after, **kwargs)

        def counts(doc, span):
            h, last = span
            rows = doc.lines[h + 1:last + 1]
            return (sum(_is_head(l.content) for l in rows),
                    sum(_is_head(l.content) and l.kind is Kind.PROSE for l in rows))

        raw_b, fa_b = counts(before, prev.spans[0])
        raw_a, fa_a = counts(after, loc.spans[0])
        grew = raw_a > raw_b and fa_a > fa_b
        return fa_a - fa_b if grew else 0
    return pin_growth
