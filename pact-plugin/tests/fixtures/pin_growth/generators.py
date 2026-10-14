"""The two random generators of the pin-growth certification, with construction labels.

Each case is a CLAUDE.md before a change and after it, built by random operations
on a list of pins. The label comes from what the operations did, never from the rule
under test or the finder:

- growth = pins added minus pins deleted is above zero. A pin revealed by removing a
  stray fence adds nothing; a hidden pin that is deleted counts as deleted.
- engaged = the naive oracle (claude_md_fence_oracle) counts more than 12 pins in the
  Pinned section after the change. A growth case that is not engaged is allowed.

Each case also records construction features, which name the signed-off family of a
growth case the rule allows (see classify()).

The random streams are ported draw for draw from the scratch model the rule was
measured on, so a seed here gives the change the model measured. Only stdlib
random.Random is used, and nothing depends on hash order.
"""

import copy
import hashlib
import random
import re
from typing import Dict, FrozenSet, Iterator, List, NamedTuple, Optional, Tuple

import claude_md_fence_oracle as oracle

from fixtures.pin_growth.rows import (
    F, FAMILY_BLOCK_DELETED, FAMILY_BLOCK_LEFT, FAMILY_BLOCK_MOVED_EDITED, FAMILY_HIDDEN_ADD,
    FAMILY_LINE_DELETED, FAMILY_STRAY_MOVED, FAMILY_TWIN_PAIRED,
    FAMILY_UNCLOSED_CLOSED, FAITHFUL, GROWTH, doc, with_commented_section,
)

CAP = 12
MEM_START, MEM_END = "<!-- PACT_MEMORY_START -->", "<!-- PACT_MEMORY_END -->"
PINNED_START, PINNED_END = "<!-- PACT_PINNED_START -->", "<!-- PACT_PINNED_END -->"
_PINNED_HEADING = re.compile(r"## Pinned Context\s*$")
_PINNED_TERMINATOR = re.compile(r"#{1,2}\s")
_BOUNDARY_PREFIXES = ("<!-- PACT_MEMORY_", "<!-- PACT_MANAGED_", "<!-- PACT_ROUTING_", "<!-- SESSION_")


class Case(NamedTuple):
    stream: str              # which generator stream
    index: int               # position in the stream
    pre: str
    post: str
    label: str               # FAITHFUL or GROWTH
    features: FrozenSet[str]
    kinds: Tuple[str, ...]   # the operation and format kinds the case exercised


def oracle_pin_count(text: str) -> Optional[int]:
    """Pins in the Pinned section by the naive oracle, or None when it is not located.

    The section is the one the gate counts: inside the memory block, inside the
    optional pinned pair when that is present, one prose `## Pinned Context` heading
    up to the next `#` or `##` heading or PACT boundary marker.
    """
    scan = oracle.scan(text)
    mem = oracle.find_block(scan, MEM_START, MEM_END)
    if mem.state != oracle.FOUND:
        return None
    a, b = mem.spans[0]
    scope = (a + 1, b - 1)
    pair = oracle.find_block(scan, PINNED_START, PINNED_END, scope)
    if pair.state == oracle.FOUND:
        scope = (pair.spans[0][0] + 1, pair.spans[0][1] - 1)
    elif pair.state != oracle.ABSENT:
        return None
    sec = oracle.find_section(scan, _PINNED_HEADING, _PINNED_TERMINATOR, scope,
                              stop_prefixes=_BOUNDARY_PREFIXES, unique=True)
    if sec.state != oracle.FOUND:
        return None
    h, last = sec.spans[0]
    return sum(1 for i in range(h + 1, last + 1)
               if scan.rows[i].kind == oracle.PROSE and scan.rows[i].content.startswith("### "))


# --- the first generator: transition and located streams ------------------------------

TITLES = [f"### T{i}" for i in range(8)]   # a small pool, so titles collide


def _transition_case(rnd: random.Random, bodiless_prob: float):
    """One change in the transition state: before it, pin `at` holds an unclosed fence,
    so the Pinned section cannot be located; the change closes it."""
    feats = set()

    def mk_pin(title, bodiless):
        return [title, [] if bodiless else [f"b{rnd.randint(0, 3)}"]]

    def has_head_snippet(p):
        return any(x.startswith("### ") for x in p[1])

    moved_titles = set()
    bodiless = rnd.random() < bodiless_prob
    n = rnd.choice([12, 13, 13, 14])
    pins = [mk_pin(rnd.choice(TITLES), bodiless) for _ in range(n)]
    for _ in range(rnd.randint(0, 2)):
        p = rnd.choice(pins)
        p[1] += [F, rnd.choice(TITLES), F]
    wm = [("### 2026-09-28 09:32", ("**Context**: one",)), ("### 2026-09-27 23:35", ("**Context**: two",))]
    at = rnd.randrange(n)
    pre_pins = [[t, list(b)] for t, b in pins]
    pre_pins[at][1] += [F, "code x"]
    post = [[t, list(b)] for t, b in pins]
    post[at][1] += [F, "code x", F]
    post_wm = list(wm)
    added = deleted = 0
    ops = []
    for _ in range(rnd.randint(0, 4)):
        op = rnd.choice(["rename", "move", "snippet", "add", "del", "unsnip", "wm_in", "wm_out", "wm_edit"])
        ops.append(op)
        if op == "rename" and post:
            rnd.choice(post)[0] = rnd.choice(TITLES)
        elif op == "move" and len(post) > 1:
            p = post.pop(rnd.randrange(len(post)))
            post.insert(rnd.randrange(len(post) + 1), p)
            if has_head_snippet(p):
                feats.add("moved a pin holding a ### snippet")
                moved_titles.update(x for x in p[1] if x.startswith("### "))
        elif op == "snippet" and post:
            k = rnd.randrange(len(post))
            title = rnd.choice(TITLES + [post[min(k + 1, len(post) - 1)][0]])
            post[k][1] += [F, title, F]
            feats.add("added a ### snippet")
        elif op == "add":
            post.insert(rnd.randrange(len(post) + 1), mk_pin(rnd.choice(TITLES), bodiless))
            added += 1
        elif op == "del" and post:
            gone = post.pop(rnd.randrange(len(post)))
            deleted += 1
            if has_head_snippet(gone):
                feats.add("deleted a pin holding a ### snippet")
        elif op == "unsnip":
            cands = [p for p in post if has_head_snippet(p)]
            if cands:
                p = rnd.choice(cands)
                idx = [i for i, x in enumerate(p[1]) if x.startswith("### ")]
                del p[1][rnd.choice(idx)]
                feats.add("deleted a ### line from a snippet that stays")
        elif op == "wm_in" and post_wm:
            t, b = post_wm.pop(0)
            post.append([t, list(b)])
            added += 1
        elif op == "wm_out" and post:
            gone = post.pop(rnd.randrange(len(post)))
            post_wm.insert(0, (gone[0], list(gone[1])))
            deleted += 1
            if has_head_snippet(gone):
                feats.add("moved a pin holding a ### snippet to Working Memory")
        elif op == "wm_edit" and post_wm:
            t, b = post_wm[0]
            post_wm[0] = (t + "x", b)
    fmt = rnd.choice([{}, {"nl": "\r\n"}, {"trail": "  "}, {}, {}])
    bom = rnd.random() < 0.1
    pre_text = doc([tuple(p) for p in pre_pins], wm=tuple(wm), bom=bom)
    post_text = doc([tuple(p) for p in post], wm=tuple(post_wm), **fmt)
    if any(has_head_snippet(p) for i, p in enumerate(pins) if i > at):
        feats.add("a ### snippet sat past the unclosed fence")
    if moved_titles & {p[0] for p in post}:
        feats.add("moved a ### snippet whose line repeats a pin's title")
    fkind = "crlf" if fmt.get("nl") else "trailing blanks" if fmt.get("trail") else "plain"
    kinds = tuple(ops) + (f"format: {fkind}", "bodiless" if bodiless else "bodies") + (("bom",) if bom else ())
    return pre_text, post_text, added - deleted, feats, kinds


def transition_stream(seed: int, bodiless_prob: float, count: int) -> Iterator[Case]:
    rnd = random.Random(seed)
    name = f"transition(bodiless={bodiless_prob}, seed={seed})"
    for k in range(count):
        pre, post, net, feats, kinds = _transition_case(rnd, bodiless_prob)
        yield Case(name, k, pre, post, GROWTH if net > 0 else FAITHFUL, frozenset(feats), kinds)


def _hidden_flags(pins) -> List[bool]:
    """Which pins' headings sit between a pair of stray `~~~` lines carried in bodies."""
    out, inside = [], False
    for t, body in pins:
        out.append(inside)
        for x in body:
            if x == "~~~":
                inside = not inside
    return out


def _located_case(rnd: random.Random):
    """One change in the located state; pins may hide between two stray fence lines."""
    feats = set()

    def has_head_snippet(p):
        return any(x.startswith("### ") for x in p[1])

    moved_titles = set()
    bodiless = rnd.random() < 0.5
    n = rnd.choice([12, 13, 13, 14])
    pins = [[rnd.choice(TITLES), [] if bodiless else [f"b{rnd.randint(0, 3)}"]] for _ in range(n)]
    for _ in range(rnd.randint(0, 2)):
        rnd.choice(pins)[1] += [F, rnd.choice(TITLES), F]
    strays = rnd.random() < 0.4
    if strays:
        a, b = sorted(rnd.sample(range(n), 2))
        pins[a][1] = pins[a][1] + ["~~~"]
        pins[b][1] = pins[b][1] + ["~~~"]
    pre = [[t, list(x)] for t, x in pins]
    post = [[t, list(x)] for t, x in pins]
    added = deleted = 0
    ops = []
    for _ in range(rnd.randint(1, 4)):
        op = rnd.choice(["rename", "move", "snippet", "add", "del", "unsnip", "unstray", "retitle"])
        ops.append(op)
        if op == "rename":
            rnd.choice(post)[0] = rnd.choice(TITLES)
        elif op == "move" and len(post) > 1:
            hid = _hidden_flags(post)
            i = rnd.randrange(len(post))
            p = post.pop(i)
            post.insert(rnd.randrange(len(post) + 1), p)
            if "~~~" in p[1]:
                feats.add("moved a pin carrying a stray fence line")
            if hid[i] or _hidden_flags(post)[next(j for j, q in enumerate(post) if q is p)]:
                feats.add("moved a pin into or out of a hidden region")
            if has_head_snippet(p):
                feats.add("moved a pin holding a ### snippet")
                moved_titles.update(x for x in p[1] if x.startswith("### "))
        elif op == "snippet":
            rnd.choice(post)[1] += [F, rnd.choice(TITLES), F]
            feats.add("added a ### snippet")
        elif op == "add":
            j = rnd.randrange(len(post) + 1)
            post.insert(j, [rnd.choice(TITLES), [] if bodiless else ["bn"]])
            added += 1
            if _hidden_flags(post)[j]:
                feats.add("added a pin inside a hidden region")
        elif op == "del" and len(post) > 1:
            hid = _hidden_flags(post)
            i = rnd.randrange(len(post))
            gone = post.pop(i)
            deleted += 1
            if hid[i]:
                feats.add("deleted a hidden pin")
            if "~~~" in gone[1]:
                feats.add("deleted a pin carrying a stray fence line")
            if has_head_snippet(gone):
                feats.add("deleted a pin holding a ### snippet")
        elif op == "unsnip":
            c = [p for p in post if has_head_snippet(p)]
            if c:
                p = rnd.choice(c)
                i = rnd.choice([k for k, x in enumerate(p[1]) if x.startswith("### ")])
                del p[1][i]
                feats.add("deleted a ### line from a snippet that stays")
        elif op in ("unstray", "retitle"):
            for p in post:
                p[1] = [x for x in p[1] if x != "~~~"]
            if op == "retitle":
                rnd.choice(post)[0] = rnd.choice(TITLES)
    fmt = rnd.choice([{}, {"nl": "\r\n"}, {"trail": " "}])
    pre_t, post_t = doc([tuple(p) for p in pre]), doc([tuple(p) for p in post], **fmt)
    if strays and any(_hidden_flags(pre)):
        feats.add("a pin hid between stray fence lines")
    visible = {p[0] for p, hid in zip(post, _hidden_flags(post)) if not hid}
    if moved_titles & visible:
        feats.add("moved a ### snippet whose line repeats a pin's title")
    fkind = "crlf" if fmt.get("nl") else "trailing blanks" if fmt.get("trail") else "plain"
    kinds = tuple(ops) + (f"format: {fkind}", "bodiless" if bodiless else "bodies") + (("strays",) if strays else ())
    return pre_t, post_t, added - deleted, feats, kinds


def located_stream(seed: int, count: int) -> Iterator[Case]:
    """`count` cases; a case whose Pinned section the oracle cannot locate before or
    after the change is skipped, as the model skipped it, so fewer may come out."""
    rnd = random.Random(seed)
    name = f"located(seed={seed})"
    for k in range(count):
        pre, post, net, feats, kinds = _located_case(rnd)
        if oracle_pin_count(pre) is None or oracle_pin_count(post) is None:
            continue
        yield Case(name, k, pre, post, GROWTH if net > 0 else FAITHFUL, frozenset(feats), kinds)


# --- the second generator: real PACT pin layouts --------------------------------------

FENCE_KINDS = [("```", "```python", "```"), ("~~~", "~~~", "~~~"), ("````", "````md", "````")]


class _Pin:
    def __init__(self, k, rnd):
        self.k = k
        self.comment = rnd.random() < 0.85
        self.title = (f"### {'`' + 'tok' + str(k) + '`' if rnd.random() < 0.3 else 'Pin'} {k} "
                      f"{rnd.choice(['rule', 'hazard', 'decision'])}")
        self.body = [f"body {k} line {i}" for i in range(rnd.randint(1, 4))]
        self.snip = None
        if rnd.random() < 0.45:
            self.snip = _make_snip(rnd)
        self.tail_stray = None   # a stray fence line ending this pin's body, not blank-separated


def _make_snip(rnd, heads=None):
    bare, opener, closer = rnd.choice(FENCE_KINDS)
    inner = []
    for i in range(rnd.randint(2, 6)):
        r = rnd.random()
        if r < 0.3 or heads:
            inner.append(f"### step {rnd.randint(1, 99)}")
            heads = None
        elif r < 0.4:
            other = [f for f in FENCE_KINDS if f[0][0] != bare[0] or len(f[0]) < len(bare)]
            if bare == "````":
                inner.append("```")          # a ``` line inside a ```` block is content
            elif other and bare[0] == "`":
                inner.append("~~~")          # a tilde line inside a backtick block is content
            else:
                inner.append(f"code {i}")
        else:
            inner.append(f"code {i} = {rnd.randint(0, 9)}")
    return [opener] + inner + [closer]


class Layout(NamedTuple):
    """Rendering choices that make a file look like a real one. Drawn from their own
    random stream, so the operations stay the ones the model measured."""
    list_items: Dict[int, int]       # pin k -> how many list lines end its body
    long_comments: bool              # real-length pinned comments
    wm_entries: int                  # real-shaped Working Memory entries after the first
    rc_lines: int                    # Retrieved Context lines
    snippet_rename: Optional[int]    # rename one snippet ### line in place after the change, or None


PLAIN = Layout({}, False, 0, 0, None)


def _layout(seed: int, index: int) -> Layout:
    r = random.Random(f"layout-{seed}-{index}")
    items = {k: r.randint(1, 3) for k in range(1, 400) if r.random() < 0.25}
    return Layout(items, r.random() < 0.6, r.randint(0, 3), r.randint(0, 4),
                  r.randrange(1000) if r.random() < 0.35 else None)


def _render(pins, crlf, layout: Layout, unclosed=None, wm=("### 2026-09-28 09:32", "wm entry")):
    lines = ["# User notes", "", "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->",
             "# PACT Framework and Managed Project Memory", "", "<!-- PACT_MEMORY_START -->", "## Retrieved Context", ""]
    for j in range(layout.rc_lines):
        lines.append(f"- **2026-09-{10 + j:02d}** decision record {j}: the rule it set and why (similarity 0.{80 - j})")
    if layout.rc_lines:
        lines.append("")
    lines += ["## Pinned Context", ""]
    for p in pins:
        if p.comment:
            reason = ("it is load-bearing for every session that edits the hooks, and a later change "
                      "must not undo it without a new measurement" if layout.long_comments else "it matters")
            lines.append(f"<!-- pinned: 2026-09-{(p.k % 28) + 1:02d}, reconfirmed: 2026-10-01 because {reason} -->")
        lines.append(p.title)
        lines += p.body
        for j in range(layout.list_items.get(p.k, 0)):
            lines.append(f"- detail {j} of pin {p.k}")
        if p.snip:
            s = list(p.snip)
            if unclosed is p:
                s = s[:-1]
            lines += s
        lines.append(p.tail_stray if p.tail_stray else "")
    lines += ["## Working Memory", wm[0], wm[1], ""]
    for j in range(layout.wm_entries):
        lines += [f"### 2026-09-2{j} 1{j}:00", f"**Context**: session {j} worked on the hooks",
                  f"**Goal**: record what changed in round {j}", f"**Memory ID**: {j:032x}", ""]
    lines += ["<!-- PACT_MEMORY_END -->", "", "<!-- PACT_MANAGED_END -->", ""]
    return ("\r\n" if crlf else "\n").join(lines)


def _build(rnd, n):
    pins = [_Pin(k, rnd) for k in range(1, n + 1)]
    hidden = None
    if rnd.random() < 0.3:                 # a stray pair hiding 1-2 pins, not blank-separated
        a = rnd.randrange(0, n - 3)
        m = rnd.randint(1, 2)
        ch = rnd.choice(["```", "~~~"])
        if not any(p.snip for p in pins[a:a + m + 1]):
            pins[a].tail_stray = ch
            pins[a + m].tail_stray = ch
            hidden = (a, m)
    unclosed = None
    if rnd.random() < 0.35:
        cands = [p for p in pins if p.snip and not p.tail_stray]
        if cands and hidden is None:
            unclosed = rnd.choice(cands)
    return pins, hidden, unclosed


def _faithful(rnd, pins, hidden, feats):
    """1-3 operations that never add a pin."""
    labels = []
    for _ in range(rnd.randint(1, 3)):
        op = rnd.choice(["rename", "swap", "move", "prune_add", "add_snip", "edit_snip", "typo", "reorder",
                         "comment", "depin_add", "snip_kind"])
        free = [i for i, p in enumerate(pins)
                if not p.tail_stray and not (hidden and hidden[0] <= i <= hidden[0] + hidden[1])]
        if not free:
            continue
        i = rnd.choice(free)
        if op == "rename":
            pins[i].title += " (renamed)"
        elif op == "swap":
            j = i + 1
            if j < len(pins) and j in free:
                pins[i], pins[j] = pins[j], pins[i]
            else:
                continue
        elif op == "move":
            p = pins.pop(i)
            ins = rnd.choice([0, len(pins)])
            if hidden and ins == 0 and hidden[0] == 0:
                ins = len(pins)
            pins.insert(ins, p)
            if hidden and ins == 0:
                hidden = (hidden[0] + 1, hidden[1])
            if hidden and i < hidden[0] and ins == len(pins) - 1:
                hidden = (hidden[0] - 1, hidden[1])
        elif op == "prune_add":
            gone = pins.pop(i)
            if gone.snip and any(x.startswith("### ") for x in gone.snip):
                feats.add("pruned a pin holding a ### snippet")
            if hidden and i < hidden[0]:
                hidden = (hidden[0] - 1, hidden[1])
            new = _Pin(100 + rnd.randint(0, 99), rnd)
            new.snip = None
            pins.append(new)
        elif op == "add_snip":
            if pins[i].snip is None:
                pins[i].snip = _make_snip(rnd, heads=True)
            else:
                pins[i].body.append("an extra body line")
        elif op == "edit_snip":
            if pins[i].snip and any(x.startswith("### ") for x in pins[i].snip):
                feats.add("edited a ### snippet")
            if pins[i].snip:
                s = pins[i].snip
                k = rnd.randrange(1, len(s) - 1) if len(s) > 2 else None
                if k and not s[k].startswith("### "):
                    s[k] = s[k] + " # edited"
        elif op == "typo":
            pins[i].body[0] = pins[i].body[0].replace("line", "Line", 1)
        elif op == "reorder":
            if hidden:
                continue
            rnd.shuffle(pins)
        elif op == "comment":
            pins[i].comment = not pins[i].comment
        elif op == "depin_add":
            p = pins[i]
            if i == 0:
                continue
            prev = pins[i - 1]
            if prev.tail_stray or (hidden and hidden[0] <= i - 1 <= hidden[0] + hidden[1]):
                continue
            ex = ["```markdown", p.title] + p.body + ["```"]
            if p.snip:
                continue
            prev.body += ex
            pins.pop(i)
            if hidden and i < hidden[0]:
                hidden = (hidden[0] - 1, hidden[1])
            new = _Pin(200 + rnd.randint(0, 99), rnd)
            new.snip = None
            pins.append(new)
        elif op == "snip_kind":
            if pins[i].snip and any(x.startswith("### ") for x in pins[i].snip):
                feats.add("re-fenced a ### snippet")
            if pins[i].snip:
                bare, opener, closer = rnd.choice(FENCE_KINDS)
                inner = pins[i].snip[1:-1]
                if any(x.startswith(bare[0] * 3) for x in inner):
                    continue
                pins[i].snip = [opener] + inner + [closer]
        labels.append(op)
    return pins, labels


def _layout_case(rnd, growth, layout: Layout):
    feats = set()
    pins, hidden, unclosed = _build(rnd, 13)
    crlf = rnd.random() < 0.25
    pre = _render(pins, crlf, layout, unclosed=unclosed)
    if unclosed is not None and any(x.startswith("### ") for x in unclosed.snip):
        feats.add("closed an unclosed ### snippet")
    post_pins, labels = _faithful(rnd, copy.deepcopy(pins), hidden, feats)
    if feats & {"edited a ### snippet", "re-fenced a ### snippet"} and {"reorder", "move", "swap"} & set(labels):
        feats.add("moved and edited a ### snippet")
    if layout.snippet_rename is not None:
        cands = [p for p in post_pins if p.snip and any(x.startswith("### ") for x in p.snip)]
        if cands:
            p = cands[layout.snippet_rename % len(cands)]
            k = next(i for i, x in enumerate(p.snip) if x.startswith("### "))
            p.snip[k] += " (renamed)"
            feats.add("renamed a ### line inside a snippet")
            # any unclosed fence makes every row from the file's first fence opener on
            # uncertain, and closing it can re-fence lines the text before rendered as headings
            if unclosed is not None and unclosed.k != p.k:
                feats.add("renamed a ### line in another snippet while a fence is unclosed")
            before = [q.k for q in pins]
            after = [q.k for q in post_pins]
            if p.k in before:
                # moved when the surviving pins before it are no longer the same ones
                survivors = set(before) & set(after)
                if ({q for q in before[:before.index(p.k)] if q in survivors}
                        != {q for q in after[:after.index(p.k)] if q in survivors}):
                    feats.add("moved and edited a ### snippet")
    if growth:
        new = _Pin(300 + rnd.randint(0, 99), rnd)
        if rnd.random() < 0.5:
            new.snip = None
        post_pins.insert(rnd.randrange(0, len(post_pins) + 1) if not hidden else len(post_pins), new)
        labels.append("add")
        if new.snip and any(x.startswith("### ") for x in new.snip):
            feats.add("the added pin holds a ### snippet")
    post_crlf = crlf if rnd.random() < 0.8 else not crlf
    post = _render(post_pins, post_crlf, layout)
    if hidden:
        feats.add("a pin hid between stray fence lines")
    kinds = tuple(labels) + (f"format: {'crlf' if crlf else 'lf'}",) + (("line endings changed",) if post_crlf != crlf else ())
    kinds += ("unclosed",) if unclosed is not None else ()
    return pre, post, feats, kinds


def layout_stream(seed: int, count: int, layout=True) -> Iterator[Case]:
    """The second generator. With layout=False it renders the model's plain layout."""
    rnd = random.Random(seed)
    name = f"layouts(seed={seed})"
    for k in range(count):
        growth = rnd.random() < 0.3
        lay = _layout(seed, k) if layout else PLAIN
        pre, post, feats, kinds = _layout_case(rnd, growth, lay)
        if layout:
            kinds += tuple(sorted(
                (["list items"] if lay.list_items else []) + (["long comments"] if lay.long_comments else [])
                + (["Working Memory entries"] if lay.wm_entries else []) + (["Retrieved Context lines"] if lay.rc_lines else [])
                + (["snippet line renamed"] if "renamed a ### line inside a snippet" in feats else [])))
        yield Case(name, k, pre, post, GROWTH if growth else FAITHFUL, frozenset(feats), kinds)


# --- a commented-out old Pinned section above the real one -----------------------------

def commented_stream(cases: Iterator[Case]) -> Iterator[Case]:
    for c in cases:
        yield c._replace(stream="commented " + c.stream, pre=with_commented_section(c.pre),
                         post=with_commented_section(c.post),
                         features=c.features | {"a commented-out old Pinned section sits above the real one"})


def stream_digest(cases) -> str:
    """sha256 over every field of each case, in order."""
    h = hashlib.sha256()
    for c in cases:
        for part in (c.stream, str(c.index), c.pre, c.post, c.label, "\x1f".join(sorted(c.features)), "\x1f".join(c.kinds)):
            data = part.encode("utf-8")
            h.update(len(data).to_bytes(8, "big"))
            h.update(data)
    return h.hexdigest()


def determinism_digests() -> Dict[str, str]:
    """The digest of the first 200 cases of each stream the slice starts with."""
    def first200(it):
        out = []
        for c in it:
            out.append(c)
            if len(out) == 200:
                break
        return out
    return {
        "transition, bodies half the time": stream_digest(first200(transition_stream(1, 0.5, 200))),
        "transition, bodies always": stream_digest(first200(transition_stream(1, 0.0, 200))),
        "located": stream_digest(first200(located_stream(1, 400))),
        "real layouts": stream_digest(first200(layout_stream(1, 200))),
    }


# --- notes outside PACT's managed block ------------------------------------------------

NOTES_ABOVE = (
    ("a Pinned Context heading", ["## Pinned Context", "", "my own list of things to keep in mind", ""]),
    ("a Pinned Context heading over ### notes", ["## Pinned Context", "", "### my pinned idea", "why it matters",
                                                 "### another idea", ""]),
)
NOTES_BELOW = (
    ("a Working Memory heading", ["# My notes", "", "## Working Memory", "my own working notes", ""]),
    ("a Retrieved Context heading", ["# My notes", "", "## Retrieved Context", "links I keep", ""]),
    ("a fenced Working Memory literal", ["# My notes", "", "```markdown", "## Working Memory",
                                         "### 2026-01-01 10:00", "an example entry", "```", ""]),
    ("a fenced Retrieved Context literal", ["# My notes", "", "~~~", "## Retrieved Context", "- an example line",
                                            "~~~", ""]),
    ("### notes under a Working Memory heading", ["# My notes", "", "## Working Memory", "### my entry",
                                                  "what I did", ""]),
)
EDITS = ("unedited", "a line added below", "a heading below renamed", "a fenced literal added below",
         "the notes below removed", "a ### line added above")


def _with_notes(text, above, below):
    nl = "\r\n" if "\r\n" in text else "\n"
    start = "<!-- PACT_MANAGED_START"
    i = text.index(start)
    if above:
        text = text[:i] + nl.join(above) + nl + text[i:]
    if below:
        end = "<!-- PACT_MANAGED_END -->"
        j = text.index(end) + len(end)
        rest = text[j:]
        lead = rest[:len(nl)] if rest.startswith(nl) else ""
        text = text[:j] + lead + nl + nl.join(below) + rest[len(lead):]
    return text


def outside_stream(cases: Iterator[Case], seed: int) -> Iterator[Case]:
    """Each case with user notes above and below PACT's managed block, the same before and
    after the change or edited by it. Drawn from their own random stream; the notes never
    touch the memory block, so the label stays the case's own."""
    for c in cases:
        r = random.Random(f"outside-{seed}-{c.index}")
        above_kind, above = r.choice(NOTES_ABOVE) if r.random() < 0.6 else ("none", [])
        below_kind, below = r.choice(NOTES_BELOW) if r.random() < 0.85 else ("none", [])
        edit = r.choice(EDITS)
        post_above, post_below = list(above), list(below)
        if edit == "a line added below" and below:
            post_below.insert(len(post_below) - 1, "a line added later")
        elif edit == "a heading below renamed" and below:
            post_below = [x + " (mine)" if x.startswith("## ") else x for x in post_below]
        elif edit == "a fenced literal added below":
            post_below = post_below + ["```markdown", "## Working Memory", "### an added example", "```", ""]
        elif edit == "the notes below removed":
            post_below = []
        elif edit == "a ### line added above" and above:
            post_above = post_above[:-1] + ["### a later idea", ""]
        else:
            edit = "unedited"
        kinds = c.kinds + (f"notes above: {above_kind}", f"notes below: {below_kind}", f"notes edit: {edit}")
        yield c._replace(stream="notes outside " + c.stream, pre=_with_notes(c.pre, above, below),
                         post=_with_notes(c.post, post_above, post_below), kinds=kinds)


# --- families --------------------------------------------------------------------------

_FEATURE_FAMILY = {
    "deleted a pin holding a ### snippet": FAMILY_BLOCK_DELETED,
    "pruned a pin holding a ### snippet": FAMILY_BLOCK_DELETED,
    "deleted a ### line from a snippet that stays": FAMILY_LINE_DELETED,
    "moved a pin holding a ### snippet to Working Memory": FAMILY_BLOCK_LEFT,
    "closed an unclosed ### snippet": FAMILY_UNCLOSED_CLOSED,
    "moved and edited a ### snippet": FAMILY_BLOCK_MOVED_EDITED,
    "moved a pin carrying a stray fence line": FAMILY_STRAY_MOVED,
    "added a pin inside a hidden region": FAMILY_HIDDEN_ADD,
    "moved a ### snippet whose line repeats a pin's title": FAMILY_TWIN_PAIRED,
}


def classify(case: Case) -> FrozenSet[str]:
    """The signed-off families a growth case's construction puts it in."""
    return frozenset(_FEATURE_FAMILY[f] for f in case.features if f in _FEATURE_FAMILY)
