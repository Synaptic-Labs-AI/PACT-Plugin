"""The fixed rows of the pin-growth certification: 202 hand-built changes to a CLAUDE.md.

Each row is a text before a change, a text after it, and what the change does to the
pins, decided by how the row is built and never by the rule under test:

- FAITHFUL: pins added minus pins deleted is at most zero. A pin revealed by removing
  a stray fence, and a hidden pin that leaves, both count as faithful.
- GROWTH: the change adds pins. A growth row with a family is one of the under-blocks
  the user signed off; the rule may allow it. A growth row without a family must be
  denied.

The texts are ported byte for byte from the scratch model the rule was designed on;
FIXED_ROWS_DIGEST pins them, so an edit to a builder below shows up as a digest
change. Generated texts too long to build by hand live in data_rows.json.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import List, NamedTuple, Optional

FAITHFUL, GROWTH = "faithful", "growth"

# The signed-off under-block families, in words. A growth case the rule allows must
# belong to exactly one of them.
FAMILY_BLOCK_DELETED = "a fenced block holding ### lines deleted, plus pins added"
FAMILY_LINE_DELETED = "a ### line deleted from a fenced block that stays, plus a pin added"
FAMILY_LINE_UNFENCED = "a fenced ### line turned into a pin, plus pins added"
FAMILY_BLOCK_LEFT = "a fenced block holding ### lines moved out of the Pinned section, plus pins added"
FAMILY_UNCLOSED_CLOSED = "an unclosed snippet closed after its ### lines, plus pins added"
FAMILY_SECTION_RENAMED = "the Working Memory heading renamed while its entries go, plus pins added"
FAMILY_BLOCK_MOVED_EDITED = "a fenced block holding ### lines moved and edited, plus pins added"
FAMILY_STRAY_MOVED = "a stray fence line moved so a pin it hides sits in a reshaped block, plus a pin added"
FAMILY_HIDDEN_ADD = "a pin added inside a region a stray fence pair hides"
FAMILY_TWIN_PAIRED = ("a moved fenced ### line paired with a pin, or a ### line in another fenced block, of the "
                      "same title, plus pins added")
FAMILY_HEADING_ENDS_PINNED = "a # or ## line inside the Pinned section ends it, so pins added below it are not counted"
FAMILY_RENAMED_PAST_UNCLOSED = ("a ### line renamed inside a code example while a fence somewhere in the file is "
                                "unclosed, beside a fence-shaped line that cannot close the example, plus a pin added")
FAMILY_NESTED_EXAMPLE = ("an example fenced inside another example leaves a stray fence line that reshapes the block "
                         "around kept snippet lines, plus a pin added")
FAMILY_SECOND_MEMORY_END = ("notes below PACT's block hold a copy of the memory-end line after a Working Memory or "
                            "Retrieved Context line, while a fence in the Pinned section is unclosed, plus pins added")
# Not an under-block of the rule: the Pinned section cannot be located after the change
# (an unclosed fence, the only heading hidden in a comment, or a second heading that a
# comment closed mid-line leaves visible), so the gate allows with an advisory whatever
# the pins do. Signed off with the rest.
FAMILY_NOT_LOCATED = "the Pinned section cannot be located after the change, so the gate allows with an advisory"
FAMILIES = (
    FAMILY_BLOCK_DELETED, FAMILY_LINE_DELETED, FAMILY_LINE_UNFENCED, FAMILY_BLOCK_LEFT,
    FAMILY_UNCLOSED_CLOSED, FAMILY_SECTION_RENAMED, FAMILY_BLOCK_MOVED_EDITED,
    FAMILY_STRAY_MOVED, FAMILY_HIDDEN_ADD, FAMILY_TWIN_PAIRED, FAMILY_HEADING_ENDS_PINNED,
    FAMILY_RENAMED_PAST_UNCLOSED, FAMILY_SECOND_MEMORY_END, FAMILY_NESTED_EXAMPLE, FAMILY_NOT_LOCATED,
)

F = "```"
BOM = "﻿"
DATA = json.loads((Path(__file__).parent / "data_rows.json").read_text(encoding="utf-8"))


class Row(NamedTuple):
    key: str                 # "<source>:<slug>", unique
    text: str                # what the change does
    pre: Optional[str]       # None: the file did not exist
    post: str
    label: str               # FAITHFUL or GROWTH
    family: Optional[str]    # set only on a signed-off growth row


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class _Builder:
    def __init__(self) -> None:
        self.rows: List[Row] = []

    def add(self, source: str, text: str, verdict: str, pre: Optional[str], post: str,
            family: Optional[str] = None) -> None:
        """verdict is the scratch model's hand label: ALLOW or DENY."""
        assert verdict in ("ALLOW", "DENY"), verdict
        label = GROWTH if (verdict == "DENY" or family) else FAITHFUL
        key = f"{source}:{slug(text)}"
        assert all(r.key != key for r in self.rows), key
        self.rows.append(Row(key, text, pre, post, label, family))


# --- the bodiless layout (no date comments): pins are "### P<i>" ----------------------

def doc(pins, wm=(("### 2026-09-28 09:32", ["**Context**: wm one"]),
                  ("### 2026-09-27 23:35", ["**Context**: wm two"])),
        head=(), nl="\n", bom=False, trail=""):
    out = ["<!-- PACT_MANAGED_START: Managed by pact-plugin -->", "# PACT", "",
           "<!-- SESSION_START -->", "## Current Session", "- Resume: x",
           "<!-- SESSION_END -->", "", *head, "<!-- PACT_MEMORY_START -->",
           "## Retrieved Context", "", "## Pinned Context", ""]
    for t, body in pins:
        out += [t, *body, ""]
    out += ["## Working Memory", ""]
    for t, body in wm:
        out += [t, *body, ""]
    out += ["<!-- PACT_MEMORY_END -->", "", "<!-- PACT_MANAGED_END -->", ""]
    text = nl.join(l + trail if l else l for l in out)
    return (BOM if bom else "") + text


def base(n=13):
    return [(f"### P{i}", [f"body {i}"]) for i in range(1, n + 1)]


def with_unclosed(pins, at=3):
    """P<at>'s body gets an unclosed fence (opener plus a code line)."""
    p = [list(x) for x in pins]
    p[at - 1] = (p[at - 1][0], p[at - 1][1] + [F, "code x"])
    return [tuple(x) for x in p]


def closed(pins, at=3):
    p = [list(x) for x in pins]
    p[at - 1] = (p[at - 1][0], p[at - 1][1] + [F, "code x", F])
    return [tuple(x) for x in p]


def rename(pins, i, t):
    return [(t, b) if k == i - 1 else (h, b) for k, (h, b) in enumerate(pins)]


def add_body(pins, i, extra):
    return [(h, b + extra) if k == i - 1 else (h, b) for k, (h, b) in enumerate(pins)]


def _bodiless_rows(b: _Builder) -> None:
    def S(text, verdict, pre, post, family=None):
        b.add("bodiless", text, verdict, pre, post, family)

    P = base()
    pre = doc(with_unclosed(P))
    C = closed(P)
    # faithful Writes in the transition state
    S("close only, a reveal", "ALLOW", pre, doc(C))
    S("close and rename P7", "ALLOW", pre, doc(rename(C, 7, "### P7 renamed")))
    moved = C[:4] + [C[9]] + C[4:9] + C[10:]
    S("close and move P10 above P5", "ALLOW", pre, doc(moved))
    S("close and add a fenced snippet holding two ### lines to P9", "ALLOW", pre,
      doc(add_body(C, 9, [F, "### a", "### b", F])))
    S("close, add P14 and delete P2", "ALLOW", pre,
      doc([x for x in C if x[0] != "### P2"] + [("### P14", ["body 14"])]))
    S("close and convert the whole file to CRLF", "ALLOW", pre, doc(C, nl="\r\n"))
    S("close and add trailing blanks to every line", "ALLOW", pre, doc(C, trail="  "))
    S("close and reflow every body", "ALLOW", pre, doc([(h, [x.upper() for x in bd]) for h, bd in C]))
    wm_plus = (("### P11", ["body 11"]), ("### 2026-09-28 09:32", ["**Context**: wm one"]),
               ("### 2026-09-27 23:35", ["**Context**: wm two"]))
    S("close and move P11 out to Working Memory", "ALLOW", pre,
      doc([x for x in C if x[0] != "### P11"], wm=wm_plus))
    S("close and drop a BOM", "ALLOW", doc(with_unclosed(P), bom=True), doc(C))
    S("close and add a snippet whose heading equals pin title P5", "ALLOW", pre,
      doc(add_body(C, 8, [F, "### P5", "body 5", F])))
    S("close, rename P6 and add a snippet to P9", "ALLOW", pre,
      doc(add_body(rename(C, 6, "### P6x"), 9, [F, "### a", F])))
    S("close and copy pin P6, heading and body, as a snippet in P9", "ALLOW", pre,
      doc(add_body(C, 9, [F, "### P6", "body 6", F])))
    # repeated identical lines with no anchoring context: bodiless pins
    B0 = [(f"### P{i}", []) for i in range(1, 14)]
    pre0 = doc(with_unclosed(B0, at=3))
    C0 = closed(B0, at=3)
    S("bodiless pins: close and add a snippet ### P5 to P8", "ALLOW", pre0,
      doc(add_body(C0, 8, [F, "### P5", F])))
    same = [("### Same", ["x"]) for _ in range(13)]
    pre_s = doc(with_unclosed(same, at=3))
    S("13 identical pins: close only", "ALLOW", pre_s, doc(closed(same, at=3)))
    S("13 identical pins: close and add a snippet ### Same to pin 9", "ALLOW", pre_s,
      doc(add_body(closed(same, at=3), 9, [F, "### Same", "x", F])))
    S("13 identical pins: close, delete one and add one identical", "ALLOW", pre_s,
      doc(closed(same, at=3)[:5] + closed(same, at=3)[6:] + [("### Same", ["x"])]))
    # Writes that must be denied, in the transition state
    S("close and add P14", "DENY", pre, doc(C + [("### P14", ["body 14"])]))
    S("close and add a duplicate title P4", "DENY", pre, doc(C + [("### P4", ["dup"])]))
    S("close, add P14 and convert to CRLF", "DENY", pre, doc(C + [("### P14", ["b"])], nl="\r\n"))
    wm_minus = (("### 2026-09-27 23:35", ["**Context**: wm two"]),)
    S("close and move a Working Memory heading into Pinned", "DENY", pre,
      doc(C + [("### 2026-09-28 09:32", ["**Context**: wm one"])], wm=wm_minus))
    wm_changed = (("### 2026-09-28 09:33", ["**Context**: wm one"]),
                  ("### 2026-09-27 23:36", ["**Context**: wm two"]))
    S("close, add P14 and edit two Working Memory headings", "DENY", pre,
      doc(C + [("### P14", ["b"])], wm=wm_changed))
    S("close, add P14 and P15, delete P2", "DENY", pre,
      doc([x for x in C if x[0] != "### P2"] + [("### P14", ["b"]), ("### P15", ["b"])]))
    S("missing file, a Write with 13 pins", "DENY", None, doc(P))
    Psn = add_body(P, 9, [F, "### s1", F])
    pre_sn = doc(with_unclosed(Psn))
    S("close and add a pin with an unchanged fenced snippet heading in between", "DENY", pre_sn,
      doc(closed(Psn) + [("### P14", ["b"])]))
    S("close and add a pin titled like a line inside a snippet", "DENY", pre_sn,
      doc(closed(Psn) + [("### s1", ["b"])]))
    S("bodiless pins: close and add a pin", "DENY", pre0, doc(C0 + [("### P14", [])]))
    S("13 identical pins: close and add one identical", "DENY", pre_s,
      doc(closed(same, at=3) + [("### Same", ["x"])]))
    S("close, add a pin and add trailing blanks everywhere", "DENY", pre,
      doc(C + [("### P14", ["b"])], trail="  "))
    # located controls
    P12 = base(12)
    S("located: add P13 at 12 pins", "DENY", doc(P12), doc(P12 + [("### P13", ["b"])]))
    S("located: rename at 13 pins", "ALLOW", doc(base(13)), doc(rename(base(13), 5, "### P5x")))
    # a lone stray fence line pairs with a later snippet opener, hiding pins
    stray_pins = add_body(base(13), 10, [F, "### s", F])
    pre_stray = doc(add_body(stray_pins, 2, [F]))
    S("delete a stray opener, revealing pins", "ALLOW", pre_stray, doc(stray_pins))
    S("delete a stray opener and retitle a revealed pin", "ALLOW", pre_stray,
      doc(rename(stray_pins, 5, "### P5x")))
    # two stray fence lines pair and hide P3 to P9
    two_stray = add_body(add_body(base(13), 2, [F]), 9, [F])
    S("located: delete a pairing stray pair, revealing pins", "ALLOW", doc(two_stray), doc(base(13)))
    S("located: delete a pairing stray pair and retitle revealed P5", "ALLOW", doc(two_stray),
      doc(rename(base(13), 5, "### P5x")))
    # misalignment stress: repeated identical lines, no anchoring body
    Bd = [(f"### P{i}", []) for i in range(1, 14)]
    pre_bd = doc(with_unclosed(Bd, at=3))
    Cbd = closed(Bd, at=3)
    x = [list(p) for p in Cbd if p[0] != "### P8"]
    for p in x:
        if p[0] == "### P7":
            p[1] = p[1] + [F, "### P8", F]
    S("bodiless pins: delete P8 and add a snippet ### P8 at the same spot", "ALLOW", pre_bd,
      doc([tuple(p) for p in x]))
    Bs = [list(p) for p in Bd]
    Bs[7][1] = [F, "### P9", F]
    pre_bs = doc(with_unclosed([tuple(p) for p in Bs], at=3))
    Cs = closed([tuple(p) for p in Bs], at=3)
    Cs2 = Cs[:8] + [("### P9", [])] + Cs[8:]
    S("bodiless pins: keep a snippet ### P9 and add pin P9 right after it", "DENY", pre_bs, doc(Cs2))
    y = [list(p) for p in Cbd]
    y[9] = ["```", ["### P10", "```"]]
    S("fence pin P10 into an example and add P14", "ALLOW", pre_bd,
      doc([tuple(p) for p in y] + [("### P14", [])]))
    # located: lines deleted from a snippet that stays
    Pk = add_body(base(12), 9, [F, "### s1", "### s2", F])
    pre_k = doc(Pk)

    def drop(pins, line):
        return [(h, [x for x in bd if x != line]) for h, bd in pins]

    S("located: delete one heading from a kept snippet and add P13", "DENY", pre_k,
      doc(drop(Pk, "### s1") + [("### P13", ["b"])]), family=FAMILY_LINE_DELETED)
    S("located: delete one heading from a kept snippet only, 13 pins", "ALLOW",
      doc(add_body(base(13), 9, [F, "### s1", F])),
      doc(drop(add_body(base(13), 9, [F, "### s1", F]), "### s1")))
    S("located: rename a heading inside a kept snippet, 13 pins", "ALLOW",
      doc(add_body(base(13), 9, [F, "### s1", F])),
      doc(add_body(base(13), 9, [F, "### s9", F])))
    two_stray_b = add_body(add_body([(f"### P{i}", []) for i in range(1, 14)], 2, [F]), 9, [F])
    S("bodiless pins, located: delete a pairing stray pair and retitle revealed P5", "ALLOW",
      doc(two_stray_b), doc(rename([(f"### P{i}", []) for i in range(1, 14)], 5, "### P5x")))
    S("located: reveal by deleting strays and delete a heading from another kept snippet", "ALLOW",
      doc(add_body(two_stray, 11, [F, "### s1", F])), doc(add_body(base(13), 11, [F, F])))
    S("located: reveal by deleting strays and add a fenced snippet with a ### line", "ALLOW",
      doc(two_stray), doc(add_body(base(13), 11, [F, "### ex", F])))
    S("located: reveal and add a real pin", "DENY", doc(two_stray), doc(base(13) + [("### P14", ["b"])]))
    S("located: move a pin hidden between strays out to the end", "ALLOW", doc(two_stray),
      doc([p for p in two_stray if p[0] != "### P5"] + [("### P5", ["body 5"])]))
    # hidden P5: P4's body ends with an opener, P5's body ends with a closer
    B13 = base(13)
    hid5 = [(h, bd + [F]) if h in ("### P4", "### P5") else (h, bd) for h, bd in B13]
    S("located: move hidden P5 out to the end, strays kept", "ALLOW", doc(hid5),
      doc([(h, bd + [F, F]) if h == "### P4" else (h, bd) for h, bd in B13 if h != "### P5"]
          + [("### P5", ["body 5"])]))
    x1 = [(F, ["### P10", F]) if h == "### P10" else (h, bd) for h, bd in base(13)]
    S("located: de-pin P10 by fencing it and add P14", "ALLOW", doc(base(13)),
      doc(x1 + [("### P14", ["b"])]))
    S("located: delete the strays and the pin they hid, then add P14", "ALLOW", doc(hid5),
      doc([(h, bd) for h, bd in B13 if h != "### P5"] + [("### P14", ["b"])]))
    # a moved fenced block: P9 holds a snippet with a ### line
    Pm = add_body(base(13), 9, [F, "### step", F])
    pre_m = doc(with_unclosed(Pm, at=3))
    Cm = closed(Pm, at=3)
    p9 = [p for p in Cm if p[0] == "### P9"][0]
    S("close, move P9 with its ### snippet to the top and add P14", "DENY", pre_m,
      doc([p9] + [p for p in Cm if p[0] != "### P9"] + [("### P14", ["b"])]))
    Pm12 = add_body(base(12), 9, [F, "### step", F])
    S("located: move P9 with its ### snippet to the top and add P13", "DENY", doc(Pm12),
      doc([Pm12[8]] + [p for p in Pm12 if p[0] != "### P9"] + [("### P13", ["b"])]))


# --- the dated layout: pins are "<!-- pinned: ... -->" plus "### Real pin <i>" -----------

def P(i, title=None, body=None):
    return f"<!-- pinned: 2026-09-{i:02d} -->\n### {title or f'Real pin {i}'}\n{body or f'body {i}'}\n\n"


def managed(pins, wm="", head="# User notes\n\nsome text\n\n"):
    return (head + "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
            "# PACT Framework and Managed Project Memory\n\n"
            "<!-- PACT_MEMORY_START -->\n## Retrieved Context\n\n"
            "## Pinned Context\n\n" + pins + "\n## Working Memory\n" + wm +
            "<!-- PACT_MEMORY_END -->\n\n<!-- PACT_MANAGED_END -->\n")


NEWPIN = "<!-- pinned: 2026-10-01 -->\n### New pin\nnew body\n\n"
WM = "### 2026-09-28 09:32\nwm entry one\n\n### 2026-09-27 10:00\nwm entry two\n\n"
SNIP3 = "body 3\n```markdown\n### step one\n### step two\n```\n"   # a closed snippet with ### lines
OPEN6 = "body 6\n```bash\necho hi\n"                            # pin 6's snippet is never closed
CLOSE6 = "body 6\n```bash\necho hi\n```\n"
UNK9 = "body 9\n~~~markdown\n### unk step\n~~~\n"                # past the unclosed opener before the change


def pins(n=13, over=None, extra_after=None):
    out = []
    for i in range(1, n + 1):
        out.append(P(i, body=(over or {}).get(i)))
        if extra_after and i in extra_after:
            out.append(extra_after[i])
    return "".join(out)


BASE_OVER = {3: SNIP3, 6: OPEN6, 9: UNK9}
BASE_CLOSED = {3: SNIP3, 6: CLOSE6, 9: UNK9}
PRE = managed(pins(over=BASE_OVER), wm=WM)          # pin 6 unclosed: Pinned is not located
POSTC = managed(pins(over=BASE_CLOSED), wm=WM)      # the same, closed
PRE_F = POSTC                                       # a located file before the change


def sub(t, a, b, n=1):
    assert t.count(a) >= 1, a
    return t.replace(a, b, n)


def replace_each(t, pairs):
    for a, b in pairs:
        t = t.replace(a, b)
    return t


def wm_above_pinned(t):
    t = t.replace("## Pinned Context\n\n", "## Working Memory\n" + WM + "\n## Pinned Context\n\n")
    return t.replace("\n## Working Memory\n" + WM + "<!-- PACT_MEMORY_END", "\n<!-- PACT_MEMORY_END")


def _dated_rows(b: _Builder) -> None:
    def row(text, pre, post, verdict, family=None):
        b.add("dated", text, verdict, pre, post, family)

    # faithful Writes in the transition state
    row("close only", PRE, POSTC, "ALLOW")
    row("close and rename pin 7, past the unclosed fence", PRE,
        sub(POSTC, "### Real pin 7\n", "### Real pin 7 renamed\n"), "ALLOW")
    row("close and rename pin 2, above the unclosed fence", PRE,
        sub(POSTC, "### Real pin 2\n", "### Real pin 2 renamed\n"), "ALLOW")
    row("close and add a fenced snippet with ### lines to pin 10", PRE,
        sub(POSTC, "body 10\n", "body 10\n```md\n### s1\n### s2\n```\n"), "ALLOW")
    row("close, delete pin 8 and add a pin", PRE, sub(sub(POSTC, P(8), ""), P(13), P(13) + NEWPIN), "ALLOW")
    row("close and move pin 10 above pin 2", PRE, sub(sub(POSTC, P(10), ""), P(2), P(10) + P(2)), "ALLOW")
    row("close and convert the whole file to CRLF", PRE, POSTC.replace("\n", "\r\n"), "ALLOW")
    row("close and add a BOM", PRE, BOM + POSTC, "ALLOW")
    row("close and add trailing spaces to every line", PRE, "\n".join(l + "  " for l in POSTC.split("\n")), "ALLOW")
    row("close and re-fence pin 9's snippet with four backticks and an info string", PRE,
        sub(POSTC, "~~~markdown\n### unk step\n~~~\n", "```` md\n### unk step\n````\n"), "ALLOW")
    row("close and move Working Memory above Pinned", PRE, wm_above_pinned(POSTC), "ALLOW")
    row("close by deleting the stray opener", PRE,
        sub(POSTC, "body 6\n```bash\necho hi\n```\n", "body 6\necho hi\n"), "ALLOW")
    row("close and add a heading to the user notes", PRE,
        sub(POSTC, "some text\n", "some text\n### my heading\n"), "ALLOW")
    row("close and add a Working Memory entry", PRE,
        sub(POSTC, "## Working Memory\n", "## Working Memory\n### 2026-10-02 08:00\nnew wm\n\n"), "ALLOW")
    row("close and delete a snippet ### line in pin 3, no pin added", PRE, sub(POSTC, "### step two\n", ""), "ALLOW")
    row("close and delete the unclosed snippet entirely", PRE,
        sub(POSTC, "body 6\n```bash\necho hi\n```\n", "body 6\n"), "ALLOW")
    row("close, rename pin 11 and delete pin 9's snippet ### line, no pin added", PRE,
        sub(sub(POSTC, "### Real pin 11\n", "### Real pin 11 v2\n"), "### unk step\n", ""), "ALLOW")
    row("close and rename pin 8 while pin 6's snippet holds a Working Memory heading line",
        sub(PRE, "echo hi\n", "echo hi\n## Working Memory\n"),
        sub(sub(POSTC, "echo hi\n```\n", "echo hi\n## Working Memory\n```\n"), "### Real pin 8\n", "### Real pin 8 v2\n"),
        "ALLOW")
    row("close, delete pins 7 and 8 and add one pin", PRE,
        sub(sub(sub(POSTC, P(7), ""), P(8), ""), P(13), P(13) + NEWPIN), "ALLOW")
    row("close, convert to CR-only line endings and rename", PRE,
        sub(POSTC, "### Real pin 4\n", "### Real pin 4 b\n").replace("\n", "\r"), "ALLOW")
    row("close and rewrap pin 7's body and title spacing", PRE,
        sub(POSTC, "### Real pin 7\nbody 7\n", "###  Real pin 7\nbody 7, rewrapped\nsecond line\n"), "ALLOW")
    # located Writes
    row("located: rename at the cap", PRE_F, sub(PRE_F, "### Real pin 5\n", "### Real pin 5 x\n"), "ALLOW")
    row("located: delete one pin and add one", PRE_F, sub(PRE_F, P(13), NEWPIN), "ALLOW")
    row("located: identical rewrite of a file at 13 pins", managed(pins(13)), managed(pins(13)), "ALLOW")
    row("located: rename in a file at 13 pins", managed(pins(13)),
        sub(managed(pins(13)), "### Real pin 13\n", "### Real pin 13 b\n"), "ALLOW")
    row("located: remove a stray fence pair that hid pins 4 and 5",
        managed(pins(over={3: "body 3\n```\n"}, extra_after={5: "```\n\n"})), managed(pins()), "ALLOW")
    row("located: add a fenced snippet with a ### line at the cap", PRE_F,
        sub(PRE_F, "body 10\n", "body 10\n```\n### a\n```\n"), "ALLOW")
    row("located: convert to CRLF", PRE_F, PRE_F.replace("\n", "\r\n"), "ALLOW")
    # growth Writes
    row("close and add a pin", PRE, sub(POSTC, P(13), P(13) + NEWPIN), "DENY")
    row("close, add a pin and delete a fenced ### line in pin 3", PRE,
        sub(sub(POSTC, "### step two\n", ""), P(13), P(13) + NEWPIN), "DENY", family=FAMILY_LINE_DELETED)
    row("close, add a pin and delete pin 9's snippet ### line past the unclosed fence", PRE,
        sub(sub(POSTC, "### unk step\n", ""), P(13), P(13) + NEWPIN), "DENY", family=FAMILY_LINE_DELETED)
    row("close, add a pin and delete a Working Memory entry", PRE,
        sub(sub(POSTC, "### 2026-09-27 10:00\nwm entry two\n\n", ""), P(13), P(13) + NEWPIN), "DENY")
    row("close and turn a Working Memory entry into a pin", PRE,
        sub(sub(POSTC, "### 2026-09-27 10:00\nwm entry two\n\n", ""), P(13),
            P(13) + "<!-- pinned: 2026-10-01 -->\n### 2026-09-27 10:00\nwm entry two\n\n"), "DENY")
    row("close and move a user-notes heading into Pinned", sub(PRE, "some text\n", "some text\n### my note\nnote body\n"),
        sub(sub(POSTC, "some text\n", "some text\n"), P(13),
            P(13) + "<!-- pinned: 2026-10-01 -->\n### my note\nnote body\n\n"), "DENY")
    row("close, add a pin and convert to CRLF", PRE, sub(POSTC, P(13), P(13) + NEWPIN).replace("\n", "\r\n"), "DENY")
    row("close, add a pin and add a BOM", PRE, BOM + sub(POSTC, P(13), P(13) + NEWPIN), "DENY")
    row("close, add two pins and delete one", PRE,
        sub(sub(POSTC, P(8), ""), P(13), P(13) + NEWPIN + NEWPIN.replace("New pin", "New pin 2")), "DENY")
    row("located: add a pin and delete a fenced ### line in pin 3", PRE_F,
        sub(sub(PRE_F, "### step two\n", ""), P(13), P(13) + NEWPIN), "DENY", family=FAMILY_LINE_DELETED)
    row("located: add a pin", PRE_F, sub(PRE_F, P(13), P(13) + NEWPIN), "DENY")
    row("close, add a pin and move Working Memory above Pinned", PRE,
        wm_above_pinned(sub(POSTC, P(13), P(13) + NEWPIN)), "DENY")
    row("close, add a pin, rename the Working Memory heading and delete an entry", PRE,
        sub(sub(sub(POSTC, "### 2026-09-27 10:00\nwm entry two\n\n", ""), "## Working Memory\n", "## Working memory\n"),
            P(13), P(13) + NEWPIN), "DENY", family=FAMILY_SECTION_RENAMED)
    row("close and turn pin 3's snippet lines into prose by removing its fence", PRE,
        sub(sub(POSTC, "```markdown\n### step one\n### step two\n```\n", "### step one\n### step two\n"), P(13), P(13)),
        "DENY", family=FAMILY_LINE_UNFENCED)
    row("close, delete pin 9 with its snippet and add a pin", PRE,
        sub(sub(POSTC, P(9, body=UNK9), ""), P(13), P(13) + NEWPIN), "ALLOW")
    row("close, delete pin 9's snippet ### line and pin 10, add a pin", PRE,
        sub(sub(sub(POSTC, "### unk step\n", ""), P(10), ""), P(13), P(13) + NEWPIN), "ALLOW")
    pre_tight = sub(PRE, "echo hi\n\n\n<!-- pinned: 2026-09-07 -->\n### Real pin 7\n", "echo hi\n### Real pin 7\n")
    post_tight = sub(POSTC, "echo hi\n```\n\n\n<!-- pinned: 2026-09-07 -->\n### Real pin 7\n",
                     "echo hi\n```\n### Real pin 7 renamed\n")
    row("close and rename pin 7 whose heading sits right under the unclosed snippet", pre_tight, post_tight, "ALLOW")
    row("close and rename all 13 pins", PRE,
        replace_each(POSTC, [(f"### Real pin {i}\n", f"### Renamed {i}\n") for i in range(1, 14)]), "ALLOW")
    row("close and reverse the pin order", PRE,
        managed("".join(P(i, body=BASE_CLOSED.get(i)) for i in range(13, 0, -1)), wm=WM), "ALLOW")
    row("close, rewrite every pin body and convert to CRLF", PRE,
        replace_each(POSTC, [(f"body {i}\n", f"body {i} rewritten\nmore\n") for i in range(1, 14)]).replace("\n", "\r\n"),
        "ALLOW")
    row("close and delete the whole Working Memory section", PRE, sub(POSTC, "\n## Working Memory\n" + WM, "\n"), "ALLOW")
    row("close, delete the last pin and add a pin at the top", PRE,
        sub(sub(POSTC, P(13), ""), P(1), NEWPIN + P(1)), "ALLOW")
    row("close and add a pin right after the closer", PRE,
        sub(POSTC, "echo hi\n```\n", "echo hi\n```\n\n" + NEWPIN), "DENY")
    row("close, add a pin and delete pin 9's snippet past the unclosed fence", PRE,
        sub(sub(POSTC, "~~~markdown\n### unk step\n~~~\n", ""), P(13), P(13) + NEWPIN), "DENY",
        family=FAMILY_BLOCK_DELETED)
    row("close by deleting pin 6's broken snippet and pin 7 together, add a pin", PRE,
        sub(sub(POSTC, "```bash\necho hi\n```\n\n\n" + P(7), "\n\n"), P(13), P(13) + NEWPIN), "ALLOW")
    pre_broken_h = sub(PRE, "echo hi\n", "echo hi\n### example heading\n")
    row("delete a broken snippet holding a ### line and add a pin", pre_broken_h,
        sub(sub(POSTC, "```bash\necho hi\n```\n", ""), P(13), P(13) + NEWPIN), "DENY", family=FAMILY_BLOCK_DELETED)
    row("close, delete a Working Memory literal from pin 6's snippet and rename pin 8",
        sub(PRE, "echo hi\n", "echo hi\n## Working Memory\n"), sub(POSTC, "### Real pin 8\n", "### Real pin 8 v2\n"),
        "ALLOW")
    row("close, move a pin to Working Memory and add a pin", PRE,
        sub(sub(sub(POSTC, P(8), ""), "## Working Memory\n", "## Working Memory\n### Real pin 8\nbody 8\n\n"),
            P(13), P(13) + NEWPIN), "ALLOW")
    row("close, rename pin 9's snippet ### line past the unclosed fence and add a pin", PRE,
        sub(sub(POSTC, "### unk step\n", "### unk step v2\n"), P(13), P(13) + NEWPIN), "DENY")
    row("close and rename pin 9's snippet ### line only", PRE, sub(POSTC, "### unk step\n", "### unk step v2\n"), "ALLOW")
    row("delete a stray opener and the pin swallowed under it together, add a pin", pre_tight,
        sub(sub(POSTC, "```bash\necho hi\n```\n\n\n<!-- pinned: 2026-09-07 -->\n### Real pin 7\nbody 7\n", ""),
            P(13), P(13) + NEWPIN), "ALLOW")
    row("close right after a snippet ### line that is also renamed, add a pin",
        sub(PRE, "echo hi\n", "echo hi\n### snippet head\n"),
        sub(sub(POSTC, "echo hi\n```\n", "echo hi\n### snippet head v2\n```\n"), P(13), P(13) + NEWPIN), "DENY",
        family=FAMILY_UNCLOSED_CLOSED)
    f_stray = managed(pins(over={3: "body 3\n```\n"}, extra_after={5: "```\n\n"}))
    row("located: remove a stray pair that hid pins 4 and 5 and retitle pin 4", f_stray,
        sub(managed(pins()), "### Real pin 4\n", "### Real pin 4 retitled\n"), "ALLOW")
    row("located: delete pin 3's whole snippet with its ### lines and add a pin", PRE_F,
        sub(sub(PRE_F, "```markdown\n### step one\n### step two\n```\n", ""), P(13), P(13) + NEWPIN), "DENY",
        family=FAMILY_BLOCK_DELETED)
    row("close, retitle pin 7, delete pin 8 and add a pin", PRE,
        sub(sub(sub(POSTC, "### Real pin 7\n", "### Real pin 7 t\n"), P(8), ""), P(13), P(13) + NEWPIN), "ALLOW")
    snip10 = "body 10\n~~~\n### Real pin 11\n~~~\n"
    pre_dupl = managed(pins(over={**BASE_OVER, 10: snip10}), wm=WM)
    post_dupl = managed(pins(over={**BASE_CLOSED, 10: snip10}), wm=WM)
    row("close and rename real pin 11 while pin 10's snippet holds the same title", pre_dupl,
        sub(post_dupl, "<!-- pinned: 2026-09-11 -->\n### Real pin 11\n", "<!-- pinned: 2026-09-11 -->\n### Real pin 11 renamed\n"),
        "ALLOW")
    row("close, delete the snippet copy and rename real pin 11", pre_dupl,
        sub(sub(post_dupl, "~~~\n### Real pin 11\n~~~\n", "~~~\n~~~\n"),
            "<!-- pinned: 2026-09-11 -->\n### Real pin 11\n", "<!-- pinned: 2026-09-11 -->\n### Real pin 11 b\n"), "ALLOW")
    pre_same = managed(pins(over=BASE_OVER).replace("### Real pin 11\n", "### Same\n").replace("### Real pin 12\n", "### Same\n"), wm=WM)
    post_same = managed(pins(over=BASE_CLOSED).replace("### Real pin 11\n", "### Same\n").replace("### Real pin 12\n", "### Same\n"), wm=WM)
    row("close and rename one of two identically titled pins", pre_same,
        post_same.replace("### Same\nbody 12\n", "### Same two\nbody 12\n"), "ALLOW")
    row("close and promote pin 10's snippet line into a real pin", pre_dupl,
        sub(sub(post_dupl, "~~~\n### Real pin 11\n~~~\n", "~~~\n~~~\n"), P(13),
            P(13) + "<!-- pinned: 2026-10-01 -->\n### Real pin 11\npromoted\n\n"), "DENY", family=FAMILY_LINE_UNFENCED)
    row("close and add a fenced snippet to pin 10 holding pin 11's title", PRE,
        sub(POSTC, "body 10\n", "body 10\n```md\n### Real pin 11\n```\n"), "ALLOW")
    pre_bodiless = PRE.replace("### Real pin 11\nbody 11\n", "### Real pin 11\n")
    post_bodiless = POSTC.replace("### Real pin 11\nbody 11\n", "### Real pin 11\n")
    row("close and add a fenced snippet to pin 10 holding bodiless pin 11's title", pre_bodiless,
        sub(post_bodiless, "body 10\n", "body 10\n```md\n### Real pin 11\n```\n"), "ALLOW")
    row("close and add a snippet holding bodiless pin 11's title directly above it", pre_bodiless,
        sub(post_bodiless, "<!-- pinned: 2026-09-11 -->\n### Real pin 11\n",
            "```md\n### Real pin 11\n```\n<!-- pinned: 2026-09-11 -->\n### Real pin 11\n"), "ALLOW")
    pre_f11 = PRE_F.replace("### Real pin 11\nbody 11\n", "### Real pin 11\n")
    row("located: add a snippet holding bodiless pin 11's title directly above it", pre_f11,
        sub(pre_f11, "<!-- pinned: 2026-09-11 -->\n### Real pin 11\n",
            "```md\n### Real pin 11\n```\n<!-- pinned: 2026-09-11 -->\n### Real pin 11\n"), "ALLOW")
    row("close and rewrite the last pin and the Working Memory line in one hunk", PRE,
        sub(POSTC, P(13) + "\n## Working Memory\n", P(13, title="Real pin 13 r") + "\n## Working Memory \n"), "ALLOW")
    row("close, add a pin as the last row and edit a Working Memory entry in one hunk", PRE,
        sub(POSTC, P(13) + "\n## Working Memory\n" + WM,
            P(13) + NEWPIN + "\n## Working Memory\n" + WM.replace("wm entry two", "wm entry 2")), "DENY")


RC = "## Retrieved Context\n\n"


def rc_below_pinned(t):
    t = t.replace(RC + "## Pinned Context\n\n", "## Pinned Context\n\n", 1)
    return t.replace("\n## Working Memory\n", "\n" + RC + "## Working Memory\n", 1)


def _section_rows(b: _Builder) -> None:
    def R(text, pre, post, verdict, family=None):
        b.add("sections", text, verdict, pre, post, family)

    R("close and move Retrieved Context below Pinned", PRE, rc_below_pinned(POSTC), "ALLOW")
    R("located: move Retrieved Context below Pinned", PRE_F, rc_below_pinned(PRE_F), "ALLOW")
    R("close, move Retrieved Context below Pinned and rename a pin", PRE,
      rc_below_pinned(sub(POSTC, "### Real pin 2\n", "### Real pin 2 r\n")), "ALLOW")
    R("close, move Retrieved Context below Pinned and add a pin", PRE,
      rc_below_pinned(sub(POSTC, P(13), P(13) + NEWPIN)), "DENY")
    R("located: de-pin pin 10 by indenting its heading 4 spaces and add a pin", PRE_F,
      sub(sub(PRE_F, "### Real pin 10\n", "    ### Real pin 10\n"), P(13), P(13) + NEWPIN), "ALLOW")
    R("located: de-pin pin 10 by demoting it to #### and add a pin", PRE_F,
      sub(sub(PRE_F, "### Real pin 10\n", "#### Real pin 10\n"), P(13), P(13) + NEWPIN), "ALLOW")
    R("close, de-pin pin 10 by fencing its heading and body, add a pin", PRE,
      sub(sub(POSTC, "### Real pin 10\nbody 10\n", "```\n### Real pin 10\nbody 10\n```\n"), P(13), P(13) + NEWPIN),
      "ALLOW")
    R("close, rename pin 3's snippet ### line, delete pin 7 and add a pin", PRE,
      sub(sub(sub(POSTC, "### step one\n", "### step 1\n"), P(7), ""), P(13), P(13) + NEWPIN), "ALLOW")
    R("close and move Pinned to the top of the memory block, above Retrieved Context", PRE,
      POSTC.replace(RC, "", 1).replace("\n## Working Memory\n", "\n" + RC + "## Working Memory\n", 1), "ALLOW")
    sn3 = "```markdown\n### step one\n### step two\n```\n"
    unk = "~~~markdown\n### unk step\n~~~\n"
    R("located: copy pin 3's fenced snippet byte for byte into pin 11", PRE_F, sub(PRE_F, "body 11\n", "body 11\n" + sn3), "ALLOW")
    R("close and copy pin 3's snippet into pin 11", PRE, sub(POSTC, "body 11\n", "body 11\n" + sn3), "ALLOW")
    R("close and copy pin 9's snippet from past the unclosed fence into pin 12", PRE,
      sub(POSTC, "body 12\n", "body 12\n" + unk), "ALLOW")
    R("located: copy pin 3's snippet into pins 11 and 12", PRE_F,
      sub(sub(PRE_F, "body 11\n", "body 11\n" + sn3), "body 12\n", "body 12\n" + sn3), "ALLOW")
    R("located: move pin 3's snippet to pin 11 and copy it back", PRE_F, sub(PRE_F, "body 11\n", "body 11\n" + sn3), "ALLOW")
    R("located: duplicate a whole snippet-bearing pin into a new fenced example", PRE_F,
      sub(PRE_F, "body 12\n", "body 12\n```\n<!-- pinned: 2026-09-03 -->\n### Real pin 3\n" + "body 3\n```\n"), "ALLOW")
    # a real pin heading directly between two tilde snippets, past the unclosed fence
    pre_c = PRE.replace("~~~markdown\n### unk step\n~~~\n\n\n<!-- pinned: 2026-09-10 -->\n### Real pin 10\nbody 10\n",
                        "~~~markdown\n### unk step\n~~~\n### Real pin 10\n~~~\nten\n~~~\nbody 10\n")
    post_c = POSTC.replace("~~~markdown\n### unk step\n~~~\n\n\n<!-- pinned: 2026-09-10 -->\n### Real pin 10\nbody 10\n",
                           "~~~markdown\n### unk step\n~~~\n### Real pin 10\n~~~\nten\n~~~\nbody 10\n")
    assert pre_c != PRE and post_c != POSTC
    R("close, delete pin 10's heading between two tilde fences and add a pin", pre_c,
      sub(post_c.replace("~~~\n### Real pin 10\n~~~\nten", "~~~\n~~~\nten"), P(13), P(13) + NEWPIN), "ALLOW")
    R("close and delete pin 10's heading between two tilde fences, no pin added", pre_c,
      post_c.replace("~~~\n### Real pin 10\n~~~\nten", "~~~\n~~~\nten"), "ALLOW")
    R("close only, a pin heading between two tilde fences", pre_c, post_c, "ALLOW")
    pre_w = PRE.replace("echo hi\n", "echo hi\n## Working Memory\n", 1)
    R("close, move Working Memory above Pinned and delete the Working Memory literal from pin 6's snippet",
      pre_w, wm_above_pinned(POSTC), "ALLOW")
    R("close and move Working Memory above Pinned, keeping the literal in pin 6's snippet", pre_w,
      wm_above_pinned(POSTC.replace("echo hi\n", "echo hi\n## Working Memory\n", 1)), "ALLOW")


def _literal_rows(b: _Builder) -> None:
    """A PACT section heading literal inside a fenced pin snippet, in the transition state."""
    def R(text, pre, post, verdict):
        b.add("literals", text, verdict, pre, post)

    def no_wm(t):
        i = t.rindex("\n## Working Memory\n")
        j = t.index("<!-- PACT_MEMORY_END")
        return t[:i + 1] + t[j:]

    def rc_below(t, wm=True):
        t = t.replace(RC + "## Pinned Context\n\n", "## Pinned Context\n\n", 1)
        if wm:
            i = t.rindex("\n## Working Memory\n")
            return t[:i] + "\n" + RC + t[i + 1:]
        return t.replace("<!-- PACT_MEMORY_END", RC + "<!-- PACT_MEMORY_END", 1)

    def lit(line):
        return "body 10\n~~~markdown\n" + line + "\n~~~\n"

    pre = no_wm(sub(PRE, "body 10\n", lit("## Retrieved Context")))
    post = rc_below(no_wm(sub(POSTC, "body 10\n", lit("## Retrieved Context"))), wm=False)
    R("no Working Memory, a Retrieved Context literal in pin 10: close and move Retrieved Context below Pinned",
      pre, post, "ALLOW")
    R("no Working Memory, a Retrieved Context literal in pin 10: close, move Retrieved Context below Pinned, add a pin",
      pre, sub(post, P(13), P(13) + NEWPIN), "DENY")
    pre = sub(PRE, "body 10\n", lit("## Retrieved Context"))
    post = rc_below(sub(POSTC, "body 10\n", lit("## Retrieved Context")))
    R("a Retrieved Context literal in pin 10: close and move Retrieved Context below Pinned", pre, post, "ALLOW")
    R("a Retrieved Context literal in pin 10: close, move Retrieved Context below Pinned, add a pin",
      pre, sub(post, P(13), P(13) + NEWPIN), "DENY")
    pre = no_wm(sub(PRE, "body 10\n", lit("## Working Memory")))
    post = rc_below(no_wm(sub(POSTC, "body 10\n", lit("## Working Memory"))), wm=False)
    R("no Working Memory, a Working Memory literal in pin 10: close and move Retrieved Context below Pinned",
      pre, post, "ALLOW")
    R("no Working Memory, a Working Memory literal in pin 10: close, move Retrieved Context below Pinned, add a pin",
      pre, sub(post, P(13), P(13) + NEWPIN), "DENY")
    post_b = rc_below(no_wm(POSTC), wm=False)
    R("no Working Memory: close, move Retrieved Context below Pinned and delete pin 10's Working Memory literal",
      pre, post_b, "ALLOW")
    R("no Working Memory: close, move Retrieved Context below Pinned, delete pin 10's Working Memory literal, add a pin",
      pre, sub(post_b, P(13), P(13) + NEWPIN), "DENY")


def _hidden_rows(b: _Builder) -> None:
    """Hidden-pin twins, departures and alignment artefacts, on the bodiless layout."""
    def R(text, verdict, pre, post, family=None):
        b.add("hidden", text, verdict, pre, post, family)

    B16 = base(16)
    two = add_body(add_body(B16, 4, [F]), 6, [F])          # P5 and P6 hidden by a stray pair; 14 real
    pre6 = doc(two)
    R("located: delete hidden P5 while P6 stays hidden, add P17", "ALLOW", pre6,
      doc([p for p in two if p[0] != "### P5"] + [("### P17", ["b"])]))
    R("located: delete the strays and hidden P5, revealing P6, add P17", "ALLOW", pre6,
      doc([p for p in B16 if p[0] != "### P5"] + [("### P17", ["b"])]))
    R("located: move hidden P5 out to the end, strays kept, sixteen pins", "ALLOW", pre6,
      doc([p for p in two if p[0] != "### P5"] + [("### P5", ["body 5"])]))
    hid = [(h, bd + [F]) if h in ("### P13", "### P14") else (h, bd) for h, bd in base(14)]   # P14 hidden; 13 real
    pre3 = doc(hid)
    R("located: move hidden P14 and its stray pair to Working Memory, add P15", "ALLOW", pre3,
      pre3.replace("body 13\n```\n\n### P14\nbody 14\n```\n\n## Working Memory\n\n",
                   "body 13\n\n### P15\nb\n\n## Working Memory\n\n```\n\n### P14\nbody 14\n```\n\n"))
    sn = add_body(base(13), 9, [F, "### step one", "### step two", F])
    presn = doc(sn)
    renamed = [(h, [x if x != "### step two" else "### step 2" for x in bd]) for h, bd in sn]
    R("located: rename a snippet ### line and add P14", "DENY", presn, doc(renamed + [("### P14", ["b"])]))
    R("located: rename a snippet ### line only", "ALLOW", presn, doc(renamed))
    R("rename a snippet ### line, close and add P14", "DENY",
      doc([(h, bd + [F, "code x"]) if h == "### P3" else (h, bd) for h, bd in sn]),
      doc([(h, (bd + [F, "code x", F]) if h == "### P3" else [x if x != "### step two" else "### step 2" for x in bd])
           for h, bd in sn] + [("### P14", ["b"])]))
    mv = add_body(base(13), 9, [F, "### step", F])
    pre_rm = doc([(h, bd + [F, "code x"]) if h == "### P3" else (h, bd) for h, bd in mv])
    cl = [(h, bd + [F, "code x", F]) if h == "### P3" else (h, bd) for h, bd in mv]
    R("close, move P9 with its ### snippet to the end and add P14", "DENY", pre_rm,
      doc([p for p in cl if p[0] != "### P9"] + [("### P14", ["b"])] + [p for p in cl if p[0] == "### P9"]))
    for name, verdict, text in (
            ("identical-fence-lines-snippet-added", "DENY", "a snippet added next to an identical fence pair, plus a pin"),
            ("identical-fence-lines-snippet-renamed", "DENY", "a snippet added next to an identical fence pair, a rename, plus a pin"),
            ("moved-snippet-fences-paired-two-added", "DENY", "a moved snippet whose fence lines pair elsewhere, two pins added, one moved out"),
            ("moved-snippet-fences-paired-entry-moved-in", "DENY", "a moved snippet whose fence lines pair elsewhere, an entry moved in"),
            ("moved-snippet-fences-paired-bodiless", "DENY", "bodiless pins: a moved snippet whose fence lines pair elsewhere, two added, one moved out"),
            ("hidden-pin-revealed-snippet-repeats-it", "ALLOW", "a hidden pin revealed while a new snippet repeats it")):
        R(text, verdict, DATA[name]["pre"], DATA[name]["post"])
    u8 = DATA["stray-pin-moved-past-hidden-pin"]
    R("located: a stray-bearing pin moved past a hidden pin, plus a pin", "DENY", u8["pre"], u8["post"])
    # signed-off residuals
    sn14 = add_body(base(14), 14, [F, "### step", F])
    p14 = doc(sn14)
    R("located: P14 with a ### snippet moved to Working Memory, P15 and P16 added", "ALLOW", p14,
      p14.replace("### P14\nbody 14\n```\n### step\n```\n\n## Working Memory\n\n",
                  "### P15\nb\n\n### P16\nb\n\n## Working Memory\n\n### P14\nbody 14\n```\n### step\n```\n\n"),
      family=FAMILY_BLOCK_LEFT)
    mv7 = add_body(base(13), 9, [F, "### step", F])
    p9e = ("### P9", ["body 9", F, "### step", "more", F])
    R("located: P9 moved to the top with its ### snippet edited, P14 added", "ALLOW", doc(mv7),
      doc([p9e] + [x for x in mv7 if x[0] != "### P9"] + [("### P14", ["b"])]), family=FAMILY_BLOCK_MOVED_EDITED)
    h9 = add_body(add_body(base(16), 4, [F]), 6, [F])
    k = [x[0] for x in h9].index("### P6")
    R("located: a pin added between hidden P5 and P6", "ALLOW", doc(h9),
      doc(h9[:k] + [("### P17", ["b"])] + h9[k:]), family=FAMILY_HIDDEN_ADD)


def _refence_rows(b: _Builder) -> None:
    """Re-fenced, moved and swapped snippets, stray pairs and their twins, on the dated layout."""
    def R(text, verdict, pre, post, family=None):
        b.add("snippets", text, verdict, pre, post, family)

    def add(n):
        return "".join(NEWPIN.replace("New pin", f"New pin {x}") for x in range(n))

    sn = "body 3\n```markdown\n### step one\n### step two\ntext\n```\n"
    base_t = managed(pins(13, over={3: sn}))
    for tag, new in (("tildes", sn.replace("```markdown", "~~~markdown").replace("text\n```", "text\n~~~")),
                     ("a new info string", sn.replace("```markdown", "```md")),
                     ("four backticks", sn.replace("```markdown", "````markdown").replace("text\n```", "text\n````"))):
        ref = managed(pins(13, over={3: new}))
        R(f"re-fence a ### snippet with {tag}, no pin added", "ALLOW", base_t, ref)
        R(f"re-fence a ### snippet with {tag}, plus a pin", "DENY", base_t, sub(ref, P(13), P(13) + add(1)))

    def mdoc(order, bodies, extra=""):
        return managed("".join(P(i, body=bodies.get(i)) for i in order) + extra)

    def msnip(k):
        return "body 2\n```markdown\nintro\n" + "".join(f"### heading {x}\ntext {x}\n" for x in range(k)) + "```\n"

    moved = [1] + list(range(3, 14)) + [2]
    for k in (2, 3):
        bd = {2: msnip(k)}
        R(f"move a snippet holding {k} ### lines, no pin added", "ALLOW", mdoc(range(1, 14), bd), mdoc(moved, bd))
        R(f"move a snippet holding {k} ### lines, plus a pin", "DENY", mdoc(range(1, 14), bd), mdoc(moved, bd, add(1)))
    long = "body 3\n" + "".join(f"a longer explanation, line {x}\n" for x in range(12))
    sw = [1, 4, 3, 2] + list(range(5, 14))
    for tag, s in (("a shared command", "cd pact-plugin"), ("a blank line", "")):
        bd = {3: long, 2: "body 2\n```markdown\n### step 0\n" + f"{s}\nrun the suite\n```\n",
              4: f"body 4\n```bash\n{s}\npython3 -m pytest -q\n```\n"}
        R(f"swap pins 2 and 4 across a longer pin, snippets sharing {tag}, no pin added", "ALLOW",
          mdoc(range(1, 14), bd), mdoc(sw, bd))
        R(f"swap pins 2 and 4 across a longer pin, snippets sharing {tag}, plus a pin", "DENY",
          mdoc(range(1, 14), bd), mdoc(sw, bd, add(1)))
    fl = DATA["nested-fence-line-pairs-moved-snippet"]
    R("a nested fence line pairs a moved snippet elsewhere, plus a pin", "DENY", fl["pre"], fl["post"])
    R("a nested fence line pairs a moved snippet elsewhere, no pin added", "ALLOW", fl["pre"], fl["twin"])
    bt = [f"### T{i}\n\n" for i in range(1, 14)]
    apre = managed("".join(bt[:4] + ["```\n### T4\n```\n\n"] + bt[4:]))
    rev = bt[:4] + ["### T4\n\n"] + bt[4:]

    def snp(it):
        return it[:9] + ["### T10\n```\n### T4\n```\n\n"] + it[10:]

    R("reveal a hidden T4 while a new snippet repeats it byte for byte", "ALLOW", apre, managed("".join(snp(rev))))
    R("reveal a hidden T4 while a new snippet repeats it byte for byte, plus a pin", "DENY", apre,
      managed("".join(snp(rev)) + NEWPIN))
    u7 = DATA["stray-pair-removed-snippet-repeats-title"]
    R("remove a stray pair revealing T4 while a new snippet repeats T4", "ALLOW", u7["pre"], u7["post"])
    opn = "body 6\n```markdown\n### heading a\n### heading b\ntext\n"
    npre, nclose = managed(pins(13, over={6: opn})), managed(pins(13, over={6: opn + "```\n"}))
    R("close an unclosed snippet holding two ### lines, plus a pin", "ALLOW", npre, sub(nclose, P(13), P(13) + add(1)),
      family=FAMILY_UNCLOSED_CLOSED)
    R("close an unclosed snippet holding two ### lines, plus three pins", "DENY", npre, sub(nclose, P(13), P(13) + add(3)))
    swl = "body 6\n```markdown\nexample\n"
    tpre = managed(pins(13, over={6: swl}))
    R("close a fence after a swallowed pin, fencing it, plus a pin", "ALLOW", tpre,
      sub(managed(pins(13, over={6: swl})).replace("body 7\n", "body 7\n```\n", 1), P(13), P(13) + NEWPIN))

    def tdoc(order, extra=""):
        out = []
        for i in order:
            out.append(P(2, body="body 2\n```markdown\n### Setup\nrun the suite\n```") if i == 2 else
                       P(4, title="Setup", body="body 4") if i == 4 else P(3, body=long) if i == 3 else P(i))
        return managed("".join(out) + extra)

    R("swap a snippet's pin past a pin titled like its ### line, plus a pin", "ALLOW", tdoc(range(1, 14)),
      tdoc(sw, NEWPIN), family=FAMILY_TWIN_PAIRED)
    R("swap a snippet's pin past a pin titled like its ### line, no pin added", "ALLOW", tdoc(range(1, 14)), tdoc(sw))

    def u8doc(order, bodies, extra=""):
        return managed("".join(P(i, body=bodies.get(i)) for i in order) + extra)

    h8 = {4: "body 4\n~~~", 7: "body 7\n~~~"}
    u8pre = u8doc(range(1, 17), h8)
    u8mv = [1, 2, 3, 5, 6, 4] + list(range(7, 17))
    R("move a stray-bearing pin below the pins it hid, plus a pin", "DENY", u8pre, u8doc(u8mv, h8, NEWPIN))
    R("move a stray-bearing pin below the pins it hid, no pin added", "ALLOW", u8pre, u8doc(u8mv, h8))
    R("move a stray-bearing pin below the pins it hid, plus a pin repeating the still-hidden pin's title", "ALLOW",
      u8pre, u8doc(u8mv, h8, NEWPIN.replace("New pin", "Real pin 7")), family=FAMILY_STRAY_MOVED)
    R("move a stray-bearing pin below the pins it hid, stray lines rewritten as backticks, plus a pin", "ALLOW",
      u8pre, u8doc(u8mv, {4: "body 4\n```", 7: "body 7\n```"}, NEWPIN), family=FAMILY_STRAY_MOVED)
    g = DATA["fence-closed-after-swallowed-pin"]
    R("close a fence after a swallowed pin, fencing it, plus a pin, generated layout", "ALLOW", g["pre"], g["post"])
    a = DATA["hidden-pin-revealed-new-snippet-repeats-it"]
    R("a hidden pin revealed while a new snippet repeats it, generated layout", "ALLOW", a["pre"], a["post"])
    a8 = {4: "body 4\n```", 5: "body 5\n```"}
    a8pre = u8doc(range(1, 15), a8)
    R("reveal hidden pin 5 while pin 9 gains a snippet repeating its title", "ALLOW", a8pre,
      u8doc(range(1, 15), {9: "body 9\n```markdown\n<!-- pinned: 2026-09-05 -->\n### Real pin 5\n```"}))
    R("reveal hidden pin 5 while a snippet right after it repeats its title", "ALLOW", a8pre,
      u8doc(range(1, 15), {5: "body 5\n```markdown\n### Real pin 5\n```"}))


# --- a commented-out old Pinned section above the real one ---------------------------

OLD_SECTION = ("<!--", "## Pinned Context", "", "### Old pin one", "old body one", "### Old pin two", "-->", "")


def with_commented_section(text: str, old=OLD_SECTION) -> str:
    """Put a closed multi-row HTML comment holding an old Pinned section, with ### lines,
    just above the real `## Pinned Context` heading. Every row of the comment is hidden,
    so the real heading stays the only one; the old ### lines must never count against
    pins added below it."""
    nl = "\r\n" if "\r\n" in text else "\n"
    marker = "## Pinned Context"
    i = text.index(marker)
    while i > 0 and text[i - 1] != "\n":
        i = text.index(marker, i + 1)
    trail = re.match(r"[ \t]*", text[i + len(marker):]).group(0)
    block = nl.join(l + trail if l else l for l in old) + nl
    return text[:i] + block + text[i:]


OLD_SECTION_THREE = ("<!--", "## Pinned Context", "", "### Old pin one", "### Old pin two", "### Old pin three",
                     "-->", "")


def build_commented_rows() -> tuple:
    """Rows with a commented-out old Pinned section above the real one. When the Pinned
    section before the change cannot be located, region R comes from the rule's region
    clause, and its start must skip the hidden heading, or the old ### lines offset as
    many pins added. These rows come from the plan, not from the scratch model, which
    has no hidden rows and reads the two headings as malformed."""
    b = _Builder()

    def R(text, verdict, pre, post, family=None):
        b.add("commented", text, verdict, pre, post, family)

    c = with_commented_section
    R("close and add a pin", "DENY", c(PRE), c(sub(POSTC, P(13), P(13) + NEWPIN)))
    R("close only", "ALLOW", c(PRE), c(POSTC))
    R("close and rename a pin", "ALLOW", c(PRE), c(sub(POSTC, "### Real pin 2\n", "### Real pin 2 renamed\n")))
    R("close, delete pin 8 and add a pin", "ALLOW", c(PRE), c(sub(sub(POSTC, P(8), ""), P(13), P(13) + NEWPIN)))
    R("located: add a pin", "DENY", c(PRE_F), c(sub(PRE_F, P(13), P(13) + NEWPIN)))
    R("located: rename a pin", "ALLOW", c(PRE_F), c(sub(PRE_F, "### Real pin 2\n", "### Real pin 2 renamed\n")))
    R("close and delete the commented section", "ALLOW", c(PRE), POSTC)
    R("close, delete the commented section and add a pin", "DENY", c(PRE), sub(POSTC, P(13), P(13) + NEWPIN))
    R("close, convert to CRLF and add a pin", "DENY", c(PRE), c(sub(POSTC, P(13), P(13) + NEWPIN)).replace("\n", "\r\n"))
    R("three old ### lines: close and add two pins", "DENY", c(PRE, OLD_SECTION_THREE),
      c(sub(POSTC, P(13), P(13) + NEWPIN + NEWPIN.replace("New pin", "New pin 2")), OLD_SECTION_THREE))
    three = with_unclosed(base(13))
    R("bodiless pins: close and add P14", "DENY", c(doc(three)), c(doc(closed(base(13)) + [("### P14", ["b"])])))
    R("bodiless pins: close only", "ALLOW", c(doc(three)), c(doc(closed(base(13)))))
    # the plan's residuals around hidden headings: each leaves the section unlocated
    hidden_real = POSTC.replace("## Pinned Context\n", "<!--\n## Pinned Context\n", 1).replace(
        "\n## Working Memory\n", "\n-->\n## Working Memory\n", 1)
    R("the only Pinned heading is inside a comment, plus a pin", "ALLOW", PRE_F,
      sub(hidden_real, P(13), P(13) + NEWPIN), family=FAMILY_NOT_LOCATED)
    mid_line = ("<!-- old notes", "## Pinned Context", "", "### Old pin one", "old notes end --> see above", "")
    R("an old heading in a comment closed mid-line, plus a pin", "ALLOW", c(PRE_F, mid_line),
      c(sub(PRE_F, P(13), P(13) + NEWPIN), mid_line), family=FAMILY_NOT_LOCATED)
    return tuple(b.rows)


def build_content_fence_rows() -> tuple:
    """A snippet ### line renamed in place while the snippet also holds a fence-shaped
    content line: a ~~~ line inside a backtick block, or a ``` line inside a ```` block.
    The renamed line pairs with its old copy when every row between the block's two
    aligned fence lines was code before the change, or when none of them is
    fence-shaped. Not in the scratch model, whose generators never renamed a snippet line."""
    b = _Builder()

    def R(text, verdict, pre, post, family=None):
        b.add("content fence", text, verdict, pre, post, family)

    for kind, snip, opener in (
            ("a ~~~ line inside a backtick block", "```python\n### step one\n~~~\n```\n", "````"),
            ("a ``` line inside a four-backtick block", "````md\n### step one\n```\n````\n", "~~~")):
        pre = managed(pins(13, over={3: "body 3\n" + snip}))
        ren = sub(pre, "### step one\n", "### step one v2\n")
        R(f"located, {kind}: rename the snippet line, plus a pin", "DENY", pre, sub(ren, P(13), P(13) + NEWPIN))
        R(f"located, {kind}: rename the snippet line only", "ALLOW", pre, ren)
        R(f"located, {kind}: rename the snippet line and add another, no pin added", "ALLOW", pre,
          sub(ren, "### step one v2\n", "### step one v2\n### step two\n"))
        # pin 6 opens a fence nothing in pin 9's snippet can close, so pin 9 sits past it,
        # on rows the text before cannot read with certainty
        unk = "body 9\n" + snip.replace("step one", "unk step")
        pre_t = managed(pins(over={3: SNIP3, 6: f"body 6\n{opener}bash\necho hi\n", 9: unk}), wm=WM)
        post_t = managed(pins(over={3: SNIP3, 6: f"body 6\n{opener}bash\necho hi\n{opener}\n", 9: unk}), wm=WM)
        ren_t = sub(post_t, "### unk step\n", "### unk step v2\n")
        R(f"close, {kind} past the unclosed fence: rename the snippet line, plus a pin", "DENY",
          pre_t, sub(ren_t, P(13), P(13) + NEWPIN))
        R(f"close, {kind} past the unclosed fence: rename the snippet line only", "ALLOW", pre_t, ren_t)
    for tilde, between in ((True, "a ~~~ block between them"), (False, "nothing fence-shaped between them")):
        pre_s, post_s = shifted_span(tilde_block=tilde)
        R(f"strays removed so a snippet's fences paired differently before, {between}: "
          "the pin between deleted, a snippet line added, plus a pin", "ALLOW", pre_s, post_s)
    pre_s, post_s = shifted_span(tilde_block=False)
    # with one stray left the rows are uncertain, and the rule reads pin 3's block as the
    # snippet the change shows it to be: its line renamed, plus a pin
    R("one stray left unpaired, so the text before is uncertain from it: the snippet line renamed, "
      "plus a pin", "DENY", sub(pre_s, "body 5\n```\n", "body 5\n"), post_s)
    for name, verdict, family, text in (
            ("renamed-snippet-line-before-a-later-unclosed-fence", "DENY", None,
             "generated layout: a snippet line beside a ~~~ line renamed above a later unclosed fence, plus a pin"),
            ("renamed-snippet-line-beside-tilde-lines-before-a-later-unclosed-fence", "DENY", None,
             "generated layout: a snippet line between ~~~ lines renamed above a later unclosed fence, plus a pin"),
            ("closing-a-fence-re-fences-rendered-headings-with-a-rename", "DENY", None,
             "generated layout: pin 1's fence closed, a later snippet line renamed, plus a pin"),
            ("new-snippet-where-a-moved-snippet-was", "ALLOW", FAMILY_LINE_DELETED,
             "generated layout: a new snippet lands where a moved snippet was, plus a pin"),
            ("moved-snippet-line-twinned-in-another-snippet", "ALLOW", FAMILY_TWIN_PAIRED,
             "generated layout: a moved snippet's line repeats a line of another snippet, plus a pin"),
            ("nested-examples-reshape-a-kept-snippet", "ALLOW", FAMILY_NESTED_EXAMPLE,
             "generated layout: a pin holding an example fenced into another example, plus a pin")):
        R(text, verdict, DATA[name]["pre"], DATA[name]["post"], family)
    return tuple(b.rows)


def shifted_span(tilde_block):
    """Before the change, stray fence lines in pins 2 and 5 pair with pin 3's snippet fences,
    so its opener closes an earlier block and its closer opens a later one, and the rows
    between them are prose: a real pin `### Old step`, and with tilde_block a closed ~~~
    block. The change removes both strays, deletes that pin, adds `### New step` inside
    pin 3's snippet and adds one pin: faithful, since one pin goes and one comes."""
    tilde = "~~~\nfoo\n~~~\n" if tilde_block else ""
    pre = managed(pins(13, over={2: "body 2\n```", 3: "body 3\n```\n### Old step\n" + tilde + "```",
                                 5: "body 5\n```"}))
    post = managed(pins(13, over={3: "body 3\n```\n### New step\n" + tilde + "```"}) + NEWPIN)
    return pre, post


def build_removal_rows() -> tuple:
    """Undated pins at and below the cap: renaming, swapping, moving or replacing one is
    allowed, and a prose ### line smuggled into a body is a pin the count catches."""
    b = _Builder()

    def R(text, verdict, pre, post):
        b.add("undated", text, verdict, pre, post)

    B = base(13)
    R("located, 13 undated pins: rename one", "ALLOW", doc(B), doc(rename(B, 7, "### P7 renamed")))
    R("located, 13 undated pins: swap two", "ALLOW", doc(B), doc(B[:4] + [B[5], B[4]] + B[6:]))
    R("located, 13 undated pins: move one to the top", "ALLOW", doc(B), doc([B[9]] + B[:9] + B[10:]))
    R("located, 13 undated pins: delete one and add one", "ALLOW", doc(B),
      doc([x for x in B if x[0] != "### P2"] + [("### P14", ["body 14"])]))
    R("located, 13 undated pins: rename every pin", "ALLOW", doc(B), doc([(h + " v2", bd) for h, bd in B]))
    B12 = base(12)
    R("located, 12 undated pins: rename one", "ALLOW", doc(B12), doc(rename(B12, 3, "### P3 renamed")))
    R("located, 12 undated pins: a pin body gains a prose ### line", "DENY", doc(B12),
      doc(add_body(B12, 5, ["### smuggled", "more body"])))
    notes = [("## My notes", ["kept inside the memory block"])]
    b.add("undated", "located, 13 pins then a ## heading inside Pinned: a pin added below the heading", "ALLOW",
          doc(B + notes), doc(B + notes + [("### P14", ["body 14"])]), FAMILY_HEADING_ENDS_PINNED)
    return tuple(b.rows)


NOTES_BELOW_WM = "\n# My notes\n\n## Working Memory\nmy own working notes\n"
NOTES_BELOW_FENCED = "\n# My notes\n\n```markdown\n## Working Memory\n### an example entry\n```\n"


def build_ceiling_rows() -> tuple:
    """User notes below PACT's managed block holding a Working Memory heading, real or
    fenced, while the Pinned section before the change is not located. Region R must stop
    at the memory block's end, or those lines stretch it over PACT's own entries."""
    b = _Builder()

    def R(text, verdict, pre, post):
        b.add("notes below", text, verdict, pre, post)

    for what, notes in (("a real Working Memory heading", NOTES_BELOW_WM),
                        ("a fenced Working Memory literal", NOTES_BELOW_FENCED)):
        R(f"notes below with {what}: close and add a pin", "DENY", PRE + notes,
          sub(POSTC, P(13), P(13) + NEWPIN) + notes)
        R(f"notes below with {what}: close only", "ALLOW", PRE + notes, POSTC + notes)
        R(f"notes below with {what}: close, delete pin 8 and add a pin", "ALLOW", PRE + notes,
          sub(sub(POSTC, P(8), ""), P(13), P(13) + NEWPIN) + notes)
    above = "## Pinned Context\n\n### my pinned idea\nwhy it matters\n### another idea\n\n"
    b.add("notes below", "notes above with a Pinned Context heading over ### lines: close and add a pin", "DENY",
          PRE.replace("<!-- PACT_MANAGED_START", above + "<!-- PACT_MANAGED_START", 1),
          sub(POSTC, P(13), P(13) + NEWPIN).replace("<!-- PACT_MANAGED_START", above + "<!-- PACT_MANAGED_START", 1))
    b.add("notes below", "notes above with a Pinned Context heading over ### lines: close only", "ALLOW",
          PRE.replace("<!-- PACT_MANAGED_START", above + "<!-- PACT_MANAGED_START", 1),
          POSTC.replace("<!-- PACT_MANAGED_START", above + "<!-- PACT_MANAGED_START", 1))
    # the only Pinned heading sits above PACT's block, so R starts there, as before the floor
    pins13 = pins(13)
    only_above = ("# User notes\n\n## Pinned Context\n\n" + pins13
                  + managed("", wm=WM, head="").replace("## Pinned Context\n\n\n", ""))
    moved_in = managed(sub(pins13, "### Real pin 4\n", "### Real pin 4 renamed\n"), wm=WM, head="# User notes\n\n")
    b.add("notes below", "the only Pinned section sits above PACT's block: move it inside and rename a pin", "ALLOW",
          only_above, moved_in)
    copy_below = "\n# My notes\n\n## Working Memory\nmine\n\n```\n<!-- PACT_MEMORY_END -->\n```\n"
    b.add("notes below", "notes below quoting the memory-end line after a Working Memory heading: close and add a pin",
          "ALLOW", PRE + copy_below, sub(POSTC, P(13), P(13) + NEWPIN) + copy_below, FAMILY_SECOND_MEMORY_END)
    return tuple(b.rows)


def build_fixed_rows() -> tuple:
    b = _Builder()
    _bodiless_rows(b)
    _dated_rows(b)
    _section_rows(b)
    _literal_rows(b)
    _hidden_rows(b)
    _refence_rows(b)
    return tuple(b.rows)


def digest(pairs) -> str:
    """sha256 over (pre, post) texts in order; a missing file hashes as a marker."""
    h = hashlib.sha256()
    for pre, post in pairs:
        for t in (pre, post):
            data = b"\x00<missing>" if t is None else t.encode("utf-8")
            h.update(len(data).to_bytes(8, "big"))
            h.update(data)
    return h.hexdigest()


FIXED_ROWS = build_fixed_rows()
COMMENTED_ROWS = build_commented_rows()
CONTENT_FENCE_ROWS = build_content_fence_rows()
REMOVAL_ROWS = build_removal_rows()
CEILING_ROWS = build_ceiling_rows()
