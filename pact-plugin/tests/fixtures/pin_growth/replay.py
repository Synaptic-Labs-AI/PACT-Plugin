"""Ordinary edits replayed on a real CLAUDE.md, for the opt-in local runner.

Given the text of a real file, cut its Pinned section into pins, pad or trim to 13 (one
over the cap), and replay the faithful operations an honest user or agent makes: each
one in the located state, and again as the Write that also closes an unclosed fence
in one of three pins. Four growth controls must be refused. Nothing here reads or
writes a file; the runner passes the text in and keeps nothing.
"""

import re
from typing import Iterator, List, Tuple

from fixtures.pin_growth.rows import BOM, FAITHFUL, GROWTH

FENCES = [("```md", "```"), ("~~~", "~~~"), ("````text", "````")]
PIN_START = re.compile(r"^(<!-- pinned:|### )")
SECTION_END = ("<!-- PACT_MEMORY_END -->", "<!-- PACT_MEMORY_PINNED_END -->")


def split_pinned(text: str) -> Tuple[str, List[str], str]:
    """(head, pins, tail): head ends with the `## Pinned Context` line and the blank lines
    after it; each pin starts at a `<!-- pinned:` line, or at a `### ` line with no
    comment before it; tail starts at the next `## ` heading, the memory-end line or the
    optional pinned-section end marker."""
    lines = text.splitlines(keepends=True)
    h = next(i for i, l in enumerate(lines) if l.rstrip("\r\n") == "## Pinned Context")
    j = h + 1
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    t = j
    while t < len(lines) and not (lines[t].startswith("## ") or lines[t].strip() in SECTION_END):
        t += 1
    pins, cur, fence = [], [], None
    for l in lines[j:t]:
        s = l.rstrip("\r\n")
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", s)
        if fence is None and PIN_START.match(s) and not (s.startswith("### ") and cur and cur[-1].startswith("<!-- pinned:")):
            if cur:
                pins.append("".join(cur))
            cur = []
        cur.append(l)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and s.strip() == m.group(1):
                fence = None
    if cur:
        pins.append("".join(cur))
    head = "".join(lines[:j])
    while pins and "### " not in pins[0] and not pins[0].startswith("<!-- pinned:"):
        head += pins.pop(0)                       # a preamble that is not a pin, such as a budget warning
    merged = []
    for c in pins:
        if merged and "### " not in merged[-1] and merged[-1].startswith("<!-- pinned:"):
            merged[-1] += c
        else:
            merged.append(c)
    return head, merged, "".join(lines[t:])


def join(head, pins, tail):
    return head + "".join(pins) + tail


def new_pin(k, nl="\n"):
    return f"<!-- pinned: 2026-10-02 -->{nl}### Added pin {k}{nl}body of added pin {k}{nl}{nl}"


def synth(k):
    return (f"<!-- pinned: 2026-09-{k:02d}, reconfirmed: 2026-10-01 because it is load-bearing -->\n"
            f"### Synthetic pin {k}: a `code` title\nFirst line of body {k}.\nSecond line, with a **bold** word.\n\n")


def base(text, n):
    h, pins, tail = split_pinned(text)
    pins = pins[:n]
    k = 1
    while len(pins) < n:
        pins.append(synth(k))
        k += 1
    return h, pins, tail


def _title_index(ls):
    return next(i for i, l in enumerate(ls) if l.startswith("### "))


def with_body_insert(pin, block):
    ls = pin.splitlines(keepends=True)
    t = _title_index(ls)
    return "".join(ls[:t + 2] + [block] + ls[t + 2:])


def rename(pin, suffix=" (renamed)"):
    ls = pin.splitlines(keepends=True)
    t = _title_index(ls)
    ls[t] = ls[t].rstrip("\r\n") + suffix + "\n"
    return "".join(ls)


def typo(pin):
    ls = pin.splitlines(keepends=True)
    t = _title_index(ls)
    for i in range(t + 1, len(ls)):
        if ls[i].strip() and not ls[i].lstrip().startswith(("```", "~~~", "<!--")):
            ls[i] = ls[i].replace("e", "E", 1) if "e" in ls[i] else ls[i].rstrip("\n") + " x\n"
            return "".join(ls)
    return pin + "typo fix\n"


def fence_pin(pin):
    ls = pin.splitlines(keepends=True)
    t = _title_index(ls)
    end = len(ls)
    while end > t + 1 and ls[end - 1].strip() == "":
        end -= 1
    return "".join(ls[:t] + ["```markdown\n"] + ls[t:end] + ["```\n"] + ls[end:])


def snippet(open_, close):
    return f"{open_}\n# Example layout\n### step one\nsome text\n### step two\n{close}\n"


def faithful_ops(h, pins, tail) -> Iterator[Tuple[str, str]]:
    n = len(pins)

    def J(ps, tl=tail):
        return join(h, ps, tl)

    for i in range(n):
        yield f"rename p{i}", J(pins[:i] + [rename(pins[i])] + pins[i + 1:])
        if i + 1 < n:
            yield f"swap p{i},p{i + 1}", J(pins[:i] + [pins[i + 1], pins[i]] + pins[i + 2:])
        yield f"move p{i} to top", J([pins[i]] + pins[:i] + pins[i + 1:])
        yield f"move p{i} to bottom", J(pins[:i] + pins[i + 1:] + [pins[i]])
        yield f"prune p{i} + add one", J(pins[:i] + pins[i + 1:] + [new_pin(1)])
        for o, c in FENCES:
            yield f"add {o} example to p{i}", J(pins[:i] + [with_body_insert(pins[i], snippet(o, c))] + pins[i + 1:])
        yield f"typo in p{i}", J(pins[:i] + [typo(pins[i])] + pins[i + 1:])
        yield f"fence p{i} into an example + add one", J(pins[:i] + [fence_pin(pins[i])] + pins[i + 1:] + [new_pin(1)])
        wm = tail.replace("## Working Memory\n", "## Working Memory\n" + pins[i], 1)
        yield f"move p{i} to Working Memory", join(h, pins[:i] + pins[i + 1:], wm)
        pc = re.sub(r"<!-- pinned: ([^>]*?) -->", r"<!-- pinned: \1, reconfirmed: 2026-10-03 -->", pins[i], count=1)
        yield f"reconfirm p{i}'s pinned comment", J(pins[:i] + [pc] + pins[i + 1:])
    yield "reverse all pins", J(list(reversed(pins)))
    yield "rename every pin", J([rename(p, " v2") for p in pins])
    full = J(pins)
    yield "CRLF whole file", full.replace("\n", "\r\n")
    yield "BOM", BOM + full
    yield "trailing blanks on every line", "\n".join(l + "  " if l else l for l in full.split("\n"))
    snips = [m.group(0) for p in pins for m in re.finditer(r"(?ms)^(```|~~~)[^\n]*\n.*?^\1\s*$\n", p)]
    for k, sn in enumerate(snips):
        for i in range(n):
            if sn not in pins[i]:
                yield f"copy existing snippet {k} into p{i}", J(pins[:i] + [with_body_insert(pins[i], sn)] + pins[i + 1:])
                break


def broken(pins, j):
    """Pin j's body gains an unclosed snippet, an honest slip."""
    return pins[:j] + [with_body_insert(pins[j], "```bash\necho unclosed\n")] + pins[j + 1:]


def fixed(pins, j):
    return pins[:j] + [with_body_insert(pins[j], "```bash\necho unclosed\n```\n")] + pins[j + 1:]


def replay(text: str) -> Iterator[Tuple[str, str, str, str]]:
    """(what, pre, post, label) for every replayed change on one file. A file whose
    Pinned section holds fewer than three pins yields nothing."""
    h0, p0, _ = split_pinned(text)
    if len(p0) < 3:
        return
    h, pins, tail = base(text, 13)
    located = list(faithful_ops(h, pins, tail))
    for what, post in located:
        yield what + " (located)", join(h, pins, tail), post, FAITHFUL
    for j in (0, len(pins) // 2, len(pins) - 1):
        closed_ops = dict(faithful_ops(h, fixed(pins, j), tail))
        for what, _ in located:
            if what in closed_ops:
                yield f"{what} (closing an unclosed fence in p{j})", join(h, broken(pins, j), tail), closed_ops[what], FAITHFUL
    h12, pins12, tail12 = base(text, 12)
    yield "add one pin at 12", join(h12, pins12, tail12), join(h12, pins12 + [new_pin(9)], tail12), GROWTH
    yield "add one pin at 13", join(h, pins, tail), join(h, pins + [new_pin(9)], tail), GROWTH
    yield "close + add one pin", join(h, broken(pins, 3), tail), join(h, fixed(pins, 3) + [new_pin(9)], tail), GROWTH
    yield "rename + add one pin", join(h, pins, tail), join(h, [rename(pins[0])] + pins[1:] + [new_pin(9)], tail), GROWTH
