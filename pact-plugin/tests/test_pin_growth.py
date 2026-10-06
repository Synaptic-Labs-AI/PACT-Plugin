"""
Location: pact-plugin/tests/test_pin_growth.py
Summary: The pin-growth rule in hooks/shared/pin_growth.py, run on the module
         directly: its allowed, denied and signed-off residual rows, the size
         bound with the step budget and the timer, the counted matcher's
         equivalence with difflib, the replace_all rows, and the module's
         import cost.
Used by: pytest. The certification populations and the clause mutants live in
         test_pin_growth_populations.py and test_pin_growth_mutants.py.

Every expected verdict comes from the plan's account of what the gate allows
and refuses, never from running the rule. "At the cap" means 12 pins; a change
is refused only when it adds pins and leaves more than 12.
"""

import difflib
import random
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pin_caps import PIN_COUNT_CAP
from shared import pin_growth
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
)
from shared.claude_md_markers import Kind, State, parse

HOOKS = Path(__file__).resolve().parent.parent / "hooks"

WM = "### 2026-09-28 09:32\nwm entry one\n\n### 2026-09-27 10:00\nwm entry two\n\n"
SNIP = "body 3\n```markdown\n### step one\n### step two\n```\n"  # a closed snippet holding ### lines
OPEN6 = "body 6\n```bash\necho hi\n"  # never closed: everything after it is uncertain
CLOSE6 = "body 6\n```bash\necho hi\n```\n"
NEW = "<!-- pinned: 2026-10-01 -->\n### New pin\nnew body\n\n"
NEW2 = "<!-- pinned: 2026-10-02 -->\n### Second new pin\nnew body two\n\n"
NEW3 = "<!-- pinned: 2026-10-03 -->\n### Third new pin\nnew body three\n\n"


def pin(i, title=None, body=None, dated=True):
    date = f"<!-- pinned: 2026-09-{i:02d} -->\n" if dated else ""
    return f"{date}### {title or f'Pin {i}'}\n{body or f'body {i}'}\n\n"


def pins(n, bodies=None, after=None, start=1):
    """Pins start..n; `bodies` replaces a pin's body, `after` appends text after it."""
    out = []
    for i in range(start, n + 1):
        out.append(pin(i, body=(bodies or {}).get(i)))
        out.append((after or {}).get(i, ""))
    return "".join(out)


def claude_md(pinned, wm=WM, notes="# User notes\n\nsome text\n\n", rc="## Retrieved Context\n\n",
              memory_head=""):
    return (notes + MANAGED_START_MARKER + "\n" + MANAGED_TITLE + "\n\n" + MEMORY_START_MARKER + "\n"
            + memory_head + rc + "## Pinned Context\n\n" + pinned + "\n## Working Memory\n" + wm
            + MEMORY_END_MARKER + "\n\n" + MANAGED_END_MARKER + "\n")


def sub(text, old, new, count=1):
    assert old in text, old
    return text.replace(old, new, count)


def visible_pins(text):
    """Fence-aware pins in the located Pinned section, or None."""
    doc = parse(text)
    located = pin_growth.locate_pinned(doc)
    if located.state is not State.FOUND:
        return None
    heading, last = located.spans[0]
    return sum(doc.lines[j].kind is Kind.PROSE and doc.lines[j].content.startswith("### ")
               for j in range(heading + 1, last + 1))


def grow(before, after, **kwargs):
    """The rule's growth, for a change whose Pinned section after is located."""
    growth = pin_growth.pin_growth(parse(before), parse(after), **kwargs)
    assert growth is not None, "the Pinned section after the change was not located"
    return growth


def engaged(after):
    pins_after = visible_pins(after)
    return pins_after is not None and pins_after > PIN_COUNT_CAP


def verdict(before, after, **kwargs):
    """The cap decision on the count axis: NOT_FOUND when the Pinned section
    after the change cannot be located, ALLOW at or under the cap, otherwise
    DENY exactly when the rule counts growth."""
    visible = visible_pins(after)
    if visible is None:
        return "NOT_FOUND"
    if visible <= PIN_COUNT_CAP:
        return "ALLOW"
    return "DENY" if grow(before, after, **kwargs) > 0 else "ALLOW"


# ---------------------------------------------------------------- base files
B12 = claude_md(pins(12, {3: SNIP}))  # at the cap; pin 3 holds a snippet with two ### lines
B13 = claude_md(pins(13, {3: SNIP}))  # already one over the cap
T13 = claude_md(pins(13, {3: SNIP, 6: OPEN6}))  # pin 6's fence is unclosed: Pinned cannot be located
C13 = claude_md(pins(13, {3: SNIP, 6: CLOSE6}))  # T13 with the fence closed: 13 pins visible
# pin 5 hidden by a stray fence pair (pin 4's body ends with ```, a ``` line follows pin 5)
H14 = claude_md(pins(14, after={4: "```\n", 5: "```\n\n"}))  # 13 visible
H14_2 = claude_md(pins(14, after={4: "```\n", 6: "```\n\n"}))  # pins 5 and 6 hidden: 12 visible


def _move(text, block, before_block):
    return sub(sub(text, block, ""), before_block, block + before_block)


def _wm_above_pinned(text):
    text = sub(text, "\n## Working Memory\n" + WM, "\n")
    return sub(text, "## Pinned Context\n\n", "## Working Memory\n" + WM + "## Pinned Context\n\n")


def _rc_below_pinned(text):
    text = sub(text, "## Retrieved Context\n\n## Pinned Context", "## Pinned Context")
    return sub(text, "\n## Working Memory\n", "\n## Retrieved Context\n\n## Working Memory\n")


TILDE9 = "body 9\n~~~\nnine\n~~~\n"
TILDE10 = "body 10\n~~~\nten\n~~~\n"
TILDES = claude_md(pins(13, {9: TILDE9, 10: TILDE10}))
WM_LITERAL = "body 9\n~~~md\n## Working Memory\n### looks like an entry\n~~~\n"
SNIP_TITLE = "body 10\n```\n### Pin 2\n```\n"
LONG6 = "body 6\n" + "".join(f"long line {k}\n" for k in range(12))
SNIP9 = "body 9\n```text\n### nine a\n### nine b\n```\n"
DL = claude_md(pins(13, {3: SNIP, 6: LONG6, 9: SNIP9}))

def ordered(order, bodies=None, extra=""):
    return claude_md("".join(pin(i, body=(bodies or {}).get(i)) for i in order) + extra)


# a stray fence pair: pin 4's body ends with an opener, pin 7's body with the closer, hiding pins 5 to 7
STRAY = {4: "body 4\n~~~", 7: "body 7\n~~~"}
STRAY_PRE = ordered(range(1, 17), STRAY)  # 13 visible
STRAY_MOVED = [1, 2, 3, 5, 6, 4] + list(range(7, 17))  # pin 4 and its opener moved below pins 5 and 6
# titles-only pins, one of them also hidden in a fenced block
TITLES = [f"### T{i}\n\n" for i in range(1, 14)]
TITLES_PRE = claude_md("".join(TITLES[:4] + ["```\n### T4\n```\n\n"] + TITLES[4:]))
TITLES_REVEALED = TITLES[:4] + ["### T4\n\n"] + TITLES[4:]
TITLES_SNIPPET = claude_md("".join(TITLES_REVEALED[:9] + ["### T10\n```\n### T4\n```\n\n"] + TITLES_REVEALED[10:]))
REVEAL_PRE = ordered(range(1, 15), {4: "body 4\n```", 5: "body 5\n```"})  # pin 5 hidden: 13 visible
SETUP_LONG = "body 3\n" + "".join(f"a longer explanation, line {k}\n" for k in range(12))
SWAP_2_4 = [1, 4, 3, 2] + list(range(5, 14))


def setup_doc(order, extra=""):
    """Pin 2 holds a snippet whose ### line repeats pin 4's title."""
    out = []
    for i in order:
        if i == 2:
            out.append(pin(2, body="body 2\n```markdown\n### Setup\nrun the suite\n```"))
        elif i == 4:
            out.append(pin(4, title="Setup", body="body 4"))
        else:
            out.append(pin(i, body=SETUP_LONG if i == 3 else None))
    return claude_md("".join(out) + extra)


# a snippet holding a fence-shaped content line that does not close it
TILDE_INSIDE = "body 3\n```python\n### step one\n~~~\n```\n"
B13_TILDE = claude_md(pins(13, {3: TILDE_INSIDE}))
T13_TILDE = claude_md(pins(13, {3: TILDE_INSIDE, 6: OPEN6}))
C13_TILDE = claude_md(pins(13, {3: TILDE_INSIDE, 6: CLOSE6}))


ALLOWED = [
    ("rename a pin", B13, sub(B13, "### Pin 5\n", "### Pin 5 renamed\n")),
    ("swap two pins", B13, sub(sub(sub(B13, pin(2), "@@2@@"), pin(9), pin(2)), "@@2@@", pin(9))),
    ("move a pin", B13, _move(B13, pin(10), pin(2))),
    ("add a fenced snippet holding ### lines", B13, sub(B13, "body 10\n", "body 10\n```md\n### s1\n### s2\n```\n")),
    ("copy a snippet into another pin", B13, sub(B13, "body 11\n", "body 11\n```markdown\n### step one\n### step two\n```\n")),
    ("re-fence a snippet from backticks to tildes", B13,
     sub(B13, "```markdown\n### step one\n### step two\n```\n", "~~~\n### step one\n### step two\n~~~\n")),
    ("re-fence a snippet to a longer fence", B13,
     sub(B13, "```markdown\n### step one\n### step two\n```\n", "````markdown\n### step one\n### step two\n````\n")),
    ("change only a snippet's info string", B13, sub(B13, "```markdown\n", "```md\n")),
    ("move a pin whose snippet holds two ### lines", B13, _move(B13, pin(3, body=SNIP), pin(12))),
    ("swap two snippet-bearing pins across a longer pin", DL,
     sub(sub(sub(DL, pin(3, body=SNIP), "@@3@@"), pin(9, body=SNIP9), pin(3, body=SNIP)), "@@3@@", pin(9, body=SNIP9))),
    ("close an unclosed fence, revealing the pins it hid", T13, C13),
    ("remove the stray opener instead of closing it", T13, sub(T13, OPEN6, "body 6\necho hi\n")),
    ("close an unclosed fence and rename a pin", T13, sub(C13, "### Pin 8\n", "### Pin 8 renamed\n")),
    ("close an unclosed fence and add a snippet", T13, sub(C13, "body 10\n", "body 10\n```\n### s\n```\n")),
    ("close an unclosed fence after a pin it swallowed, fencing it, plus one pin added",
     claude_md(pins(13, {6: OPEN6})),
     claude_md(pins(13, {6: OPEN6}, after={7: "```\n\n", 13: NEW}))),
    ("fence a pin into an example, plus one pin added", B13,
     sub(sub(B13, pin(10), "```\n" + pin(10) + "```\n\n"), pin(13), pin(13) + NEW)),
    ("delete a stray fence pair with the pin it hid, plus one added", H14,
     sub(sub(H14, "```\n" + pin(5) + "```\n\n", ""), pin(14), pin(14) + NEW)),
    ("delete a hidden pin while its stray pair still hides another, plus one added", H14_2,
     sub(sub(H14_2, pin(5), ""), pin(14), pin(14) + NEW)),
    ("move a hidden pin out of its fence", H14, sub(H14, pin(5) + "```\n\n", "```\n\n" + pin(5))),
    ("move a hidden pin to Working Memory with its stray fence lines, plus one added", H14,
     sub(sub(sub(H14, "```\n" + pin(5) + "```\n\n", ""), "## Working Memory\n", "## Working Memory\n```\n" + pin(5) + "```\n\n"),
         pin(14), pin(14) + NEW)),
    ("add one pin and delete one", B13, sub(sub(B13, pin(8), ""), pin(13), pin(13) + NEW)),
    ("move a pin to Working Memory", claude_md(pins(14)),
     sub(sub(claude_md(pins(14)), pin(9), ""), "## Working Memory\n", "## Working Memory\n" + pin(9))),
    ("move Retrieved Context below the Pinned section", B13, _rc_below_pinned(B13)),
    ("close an unclosed fence and move Retrieved Context below the Pinned section", T13, _rc_below_pinned(C13)),
    ("close an unclosed fence and move Working Memory above the Pinned section", T13, _wm_above_pinned(C13)),
    ("delete a pin heading between two tilde fences, plus one added", TILDES,
     sub(sub(TILDES, "### Pin 10\n", ""), pin(13), pin(13) + NEW)),
    ("a Working Memory literal inside a tilde-fenced snippet, close a fence and rename",
     claude_md(pins(13, {6: OPEN6, 9: WM_LITERAL})),
     sub(claude_md(pins(13, {6: CLOSE6, 9: WM_LITERAL})), "### Pin 2\n", "### Pin 2 renamed\n")),
    ("rename a snippet's ### line, no pin added", B13, sub(B13, "### step one\n", "### step 1\n")),
    ("reveal a hidden pin while a new snippet repeats its title", H14,
     sub(sub(H14, "```\n" + pin(5) + "```\n\n", pin(5)), "body 10\n", "body 10\n```\n### Pin 5\n```\n")),
    ("rewrite every line ending to CRLF", B13, B13.replace("\n", "\r\n")),
    ("rewrite every line ending to CR", B13, B13.replace("\n", "\r")),
    ("add a byte order mark", B13, "﻿" + B13),
    ("add trailing blanks to every line", B13, "\n".join(line + "  \t" for line in B13.split("\n"))),
    ("rewrite pin bodies", B13, B13.replace("body ", "rewritten body ")),
    ("a snippet line equal to a pin title, then a rename",
     claude_md(pins(13, {10: SNIP_TITLE})), sub(claude_md(pins(13, {10: SNIP_TITLE})), "### Pin 4\n", "### Pin 4 renamed\n")),
    ("move a stray-bearing pin below the pins it hid, no pin added", STRAY_PRE, ordered(STRAY_MOVED, STRAY)),
    ("reveal a hidden titles-only pin while a new snippet repeats it byte for byte", TITLES_PRE, TITLES_SNIPPET),
    ("reveal a hidden pin while another pin gains a snippet repeating its title with its date comment", REVEAL_PRE,
     ordered(range(1, 15), {9: "body 9\n```markdown\n<!-- pinned: 2026-09-05 -->\n### Pin 5\n```"})),
    ("reveal a hidden pin while its own body gains a snippet repeating its title", REVEAL_PRE,
     ordered(range(1, 15), {5: "body 5\n```markdown\n### Pin 5\n```"})),
    ("swap a snippet's pin with a pin titled like its ### line, no pin added", setup_doc(range(1, 14)), setup_doc(SWAP_2_4)),
    ("rename a snippet's ### line in place while the snippet holds a non-closing fence-shaped line",
     B13_TILDE, sub(B13_TILDE, "### step one\n", "### step 1\n")),
    ("remove two stray fences so a pin line between blocks becomes a snippet line, rename it, plus one added",
     ordered(range(1, 14), {2: "body 2\n```", 3: "body 3\n```\n### Old step\n```", 5: "body 5\n```"}),
     ordered(range(1, 14), {3: "body 3\n```\n### New step\n```"}, NEW)),
    ("thirteen identical titles, one body rewritten", claude_md("".join(pin(i, title="Same") for i in range(1, 14))),
     sub(claude_md("".join(pin(i, title="Same") for i in range(1, 14))), "body 7\n", "body seven\n")),
]

DENIED = [
    ("add a plain new pin at the cap", B12, sub(B12, pin(12), pin(12) + NEW)),
    ("add a plain new pin over the cap", B13, sub(B13, pin(13), pin(13) + NEW)),
    ("add a pin with a duplicate title", B12, sub(B12, pin(12), pin(12) + pin(4))),
    ("add a pin titled like a heading in the user's notes",
     claude_md(pins(12), notes="# User notes\n\n### Notes heading\n\n"),
     sub(claude_md(pins(12), notes="# User notes\n\n### Notes heading\n\n"), pin(12), pin(12) + "### Notes heading\nx\n\n")),
    ("add a pin titled like a line inside a snippet", B12, sub(B12, pin(12), pin(12) + "### step one\nx\n\n")),
    ("move a Working Memory entry into Pinned", B12,
     sub(sub(B12, "### 2026-09-28 09:32\nwm entry one\n\n", ""), pin(12), pin(12) + "### 2026-09-28 09:32\nwm entry one\n\n")),
    ("add a pin while Working Memory headings change", B12,
     sub(sub(B12, "### 2026-09-27 10:00\n", "### 2026-09-27 11:00\n"), pin(12), pin(12) + NEW)),
    ("add a pin with a CRLF rewrite", B12, sub(B12, pin(12), pin(12) + NEW).replace("\n", "\r\n")),
    ("close an unclosed fence, move Retrieved Context below Pinned and add a pin", T13,
     sub(_rc_below_pinned(C13), pin(13), pin(13) + NEW)),
    ("rename a snippet's ### line and add a pin", B12,
     sub(sub(B12, "### step one\n", "### step 1\n"), pin(12), pin(12) + NEW)),
    ("close an unclosed fence, rename a snippet's ### line and add a pin", T13,
     sub(sub(C13, "### step one\n", "### step 1\n"), pin(13), pin(13) + NEW)),
    ("re-fence a snippet and add a pin", B12,
     sub(sub(B12, "```markdown\n### step one\n### step two\n```\n", "~~~\n### step one\n### step two\n~~~\n"),
         pin(12), pin(12) + NEW)),
    ("move a pin together with its snippet and add a pin", B12,
     sub(_move(B12, pin(3, body=SNIP), pin(11)), pin(12), pin(12) + NEW)),
    ("reveal a hidden pin while a new snippet repeats it, plus one added", H14,
     sub(sub(sub(H14, "```\n" + pin(5) + "```\n\n", pin(5)), "body 10\n", "body 10\n```\n### Pin 5\n```\n"),
         pin(14), pin(14) + NEW)),
    ("close an unclosed snippet and add one more pin than its ### lines",
     claude_md(pins(13, {6: "body 6\n```md\n### u1\n### u2\n"})),
     claude_md(pins(13, {6: "body 6\n```md\n### u1\n### u2\n```\n"}, after={13: NEW + NEW2 + NEW3}))),
    ("a commented-out old Pinned section above the real one, then a fence closed and a pin added",
     claude_md(pins(13, {6: OPEN6}), memory_head="<!--\n## Pinned Context\n### old a\n### old b\n-->\n"),
     claude_md(pins(13, {6: CLOSE6}, after={13: NEW}), memory_head="<!--\n## Pinned Context\n### old a\n### old b\n-->\n")),
    ("a first Write with 13 pins", "", claude_md(pins(13))),
    ("rename a snippet's ### line holding a non-closing fence-shaped line, and add a pin", B13_TILDE,
     sub(sub(B13_TILDE, "### step one\n", "### step 1\n"), pin(13), pin(13) + NEW)),
    ("move a stray-bearing pin below the pins it hid, so a pin it hid stays hidden in a reshaped block, plus one added",
     STRAY_PRE, ordered(STRAY_MOVED, STRAY, NEW)),
    ("reveal a hidden titles-only pin while a new snippet repeats it, plus one added", TITLES_PRE, TITLES_SNIPPET.replace(
        "\n## Working Memory", NEW + "\n## Working Memory", 1)),
]

# Allowed past the cap: the residual under-blocks the user signed off. Each
# reads the same as a change that must be allowed.
RESIDUALS = [
    ("deleting a fenced block holding ### lines plus as many pins added (signed off: reads as deleting "
     "a stray fence pair with the pin it hid)", B13,
     sub(sub(B13, SNIP, "body 3\n"), pin(13), pin(13) + NEW + NEW2)),
    ("deleting a ### line from a fenced block that stays plus a pin added (signed off: reads as deleting "
     "a hidden pin while its stray pair still hides another)", B13,
     sub(sub(B13, "### step two\n", ""), pin(13), pin(13) + NEW)),
    ("turning fenced ### lines into real pins by removing the block's fences (signed off: reads as "
     "revealing a hidden pin)", B13, sub(B13, SNIP, "body 3\n### step one\n### step two\n")),
    ("moving a pin whose body holds a ### snippet to Working Memory plus two added (signed off: reads as "
     "moving a hidden pin to Working Memory with its stray fence lines)", B13,
     sub(sub(sub(B13, pin(3, body=SNIP), ""), "## Working Memory\n", "## Working Memory\n" + pin(3, body=SNIP)),
         pin(13), pin(13) + NEW + NEW2)),
    ("adding an unclosed snippet's closer after its ### lines plus as many pins (signed off: reads as "
     "closing a fence after a pin the opener swallowed)",
     claude_md(pins(13, {6: "body 6\n```md\n### u1\n### u2\n"})),
     claude_md(pins(13, {6: "body 6\n```md\n### u1\n### u2\n```\n"}, after={13: NEW + NEW2}))),
    ("renaming the Working Memory heading while deleting its entries and adding pins (signed off: "
     "adversarial)", T13,
     sub(sub(C13, "## Working Memory\n" + WM, "## Working Notes\n"), pin(13), pin(13) + NEW + NEW2)),
    ("moving a snippet-bearing pin and editing its snippet plus one added (signed off: reads as revealing "
     "a hidden pin while a new snippet repeats its title)", B13,
     sub(_move(B13, pin(3, body=SNIP), pin(12)).replace("### step two\n", "### step 2\n"), pin(13), pin(13) + NEW)),
    ("moving a stray-bearing pin so a hidden pin sits in a reshaped block, plus a pin repeating its title "
     "(signed off: reads as revealing that pin while a new example repeats its heading)",
     STRAY_PRE, ordered(STRAY_MOVED, STRAY, NEW.replace("New pin", "Pin 7"))),
    ("moving a stray-bearing pin so a hidden pin sits in a reshaped block, with the stray lines rewritten in "
     "backticks, plus one added (signed off: reads as removing a stray pair and fencing a pin)",
     STRAY_PRE, ordered(STRAY_MOVED, {4: "body 4\n```", 7: "body 7\n```"}, NEW)),
    ("swapping a snippet's pin past a pin titled like its ### line, plus one added (signed off: reads as "
     "revealing a hidden pin while a new snippet repeats it)", setup_doc(range(1, 14)), setup_doc(SWAP_2_4, NEW)),
    ("adding a pin inside a region a stray fence pair hides (signed off: reads as adding a snippet line)",
     claude_md(pins(15, after={4: "```\n", 6: "```\n\n"})),
     sub(claude_md(pins(15, after={4: "```\n", 6: "```\n\n"})), pin(6), NEW + pin(6))),
]

# Not counted, allowed with the not-found advisory: the Pinned section after
# the change cannot be located (signed off).
NOT_FOUND = [
    ("a first Write whose Pinned section sits past an unclosed fence, at any pin count (signed off)", "",
     claude_md(pins(20), notes="# User notes\n\n```\nunclosed\n\n")),
    ("an unclosed fence in a pin body (signed off)", B13, sub(B13, "body 6\n", OPEN6)),
    ("both PACT memory markers removed (signed off: warn once)", B13,
     sub(sub(sub(B13, MEMORY_START_MARKER + "\n", ""), MEMORY_END_MARKER + "\n", ""), pin(13), pin(13) + NEW)),
    ("the only Pinned heading on hidden rows (signed off)", B13,
     sub(sub(B13, "## Pinned Context\n", "<!--\n## Pinned Context\n-->\n"), pin(13), pin(13) + NEW)),
    ("an old Pinned heading inside a declaration beside the real one (signed off)", B13,
     sub(B13, "## Retrieved Context\n", "<!DOCTYPE x\n## Pinned Context\n>\n## Retrieved Context\n")),
]


@pytest.mark.parametrize("name, before, after", ALLOWED, ids=[r[0] for r in ALLOWED])
def test_allowed_change(name, before, after):
    assert engaged(after)  # the cap is engaged, so the rule decides
    assert verdict(before, after) == "ALLOW"


@pytest.mark.parametrize("name, before, after", DENIED, ids=[r[0] for r in DENIED])
def test_denied_change(name, before, after):
    assert verdict(before, after) == "DENY"


@pytest.mark.parametrize("name, before, after", RESIDUALS, ids=[r[0] for r in RESIDUALS])
def test_signed_off_residual_is_allowed(name, before, after):
    # each of these adds pins past the cap; the user accepted it
    assert engaged(after)
    assert verdict(before, after) == "ALLOW"


@pytest.mark.parametrize("name, before, after", NOT_FOUND, ids=[r[0] for r in NOT_FOUND])
def test_unlocatable_pinned_section_is_not_counted(name, before, after):
    assert pin_growth.pin_growth(parse(before), parse(after)) is None


def test_a_pound_line_inside_pinned_ends_it_so_pins_below_are_not_counted():
    # pre-existing and signed off: a `#` or `##` line ends the section
    before = claude_md(pins(12) + "## My notes\n\n")
    after = sub(before, "## My notes\n\n", "## My notes\n\n" + NEW + NEW2)
    assert verdict(before, after) == "ALLOW"


PLAIN_PINNED = "# notes\n\n## Pinned Context\n\n" + pins(13) + "\n## Working Memory\n" + WM


def test_a_text_with_no_memory_block_before_is_compared_by_its_pinned_heading():
    # PACT's markers added around an existing Pinned section: a rename adds nothing, a new pin is counted
    assert verdict(PLAIN_PINNED, sub(claude_md(pins(13)), "### Pin 2\n", "### Pin 2 renamed\n")) == "ALLOW"
    assert verdict(PLAIN_PINNED, sub(claude_md(pins(13)), pin(13), pin(13) + NEW)) == "DENY"


def test_a_rename_in_an_example_past_an_unclosed_fence_with_a_pin_added_is_refused():
    # Past an unclosed fence the example's rows are uncertain; the `~~~` line inside it cannot close its backtick
    # opener, so the renamed line pairs as a rename and the added pin is counted.
    after = sub(sub(C13_TILDE, "### step one\n", "### step 1\n"), pin(13), pin(13) + NEW)
    assert verdict(T13_TILDE, after) == "DENY"


def test_removing_a_stray_fence_before_an_example_renaming_its_line_and_adding_a_pin_is_refused():
    # Past the unclosed fence the example's rows are uncertain; none of them closes the example's own opener, so the
    # renamed line pairs as a rename and the added pin is counted.
    stray = claude_md(pins(13, {2: "body 2\n```", 3: "body 3\n```\n### step one\n~~~\n```"}))
    after = claude_md(pins(13, {3: "body 3\n```\n### step 1\n~~~\n```"}, after={13: NEW}))
    assert verdict(stray, after) == "DENY"


def test_closing_an_earlier_fence_renaming_a_snippet_line_and_adding_a_pin_is_refused():
    # An unclosed fence in pin 3 pairs, read in order, with the snippet's own opener in pin 6; the snippet's opener
    # is not closed by any uncertain row before its closer, so the renamed line pairs and the added pin is counted.
    snippet = "body 6\n```\n### step one\n### step two\n```"
    before = claude_md(pins(13, {3: "body 3\n```\nexample", 6: snippet}))
    after = claude_md(pins(13, {3: "body 3\n```\nexample\n```", 6: snippet.replace("step two", "step 2")},
                           after={13: NEW}))
    assert verdict(before, after) == "DENY"


def test_a_pinned_heading_of_the_users_own_above_the_block_does_not_start_the_region_before():
    notes = "# User notes\n\n## Pinned Context\n\n### my note a\n### my note b\n\n"
    before = claude_md(pins(13, {3: SNIP, 6: OPEN6}), notes=notes)
    assert verdict(before, claude_md(pins(13, {3: SNIP, 6: CLOSE6}), notes=notes).replace(
        "### Pin 2\n", "### Pin 2 renamed\n")) == "ALLOW"
    assert verdict(before, claude_md(pins(13, {3: SNIP, 6: CLOSE6}, after={13: NEW}), notes=notes)) == "DENY"


# Lines below PACT's block: a Working Memory heading of the user's own, or a fenced literal of one. The last
# memory end line caps R, so neither stretches R over PACT's Working Memory entries.
LITERAL_BELOW = "\n## My notes\n\n```\n## Working Memory\n```\n"
HEADING_BELOW = "\n## Working Memory\n\nmy own section\n"
OUTSIDE = [(tag, tail) for tag, tail in (("a fenced Working Memory literal below the block", LITERAL_BELOW),
                                         ("a Working Memory heading of the user's own below the block", HEADING_BELOW))]


@pytest.mark.parametrize("tag, tail", OUTSIDE, ids=[o[0] for o in OUTSIDE])
def test_lines_below_the_block_do_not_stretch_the_region_before(tag, tail):
    before = T13 + tail
    assert verdict(before, sub(C13, "### Pin 2\n", "### Pin 2 renamed\n") + tail) == "ALLOW"
    assert verdict(before, sub(C13, pin(13), pin(13) + NEW) + tail) == "DENY"
    assert verdict(before, sub(C13, pin(13), pin(13) + NEW + NEW2) + tail) == "DENY"


def test_signed_off_a_second_memory_end_line_below_the_block_lets_pins_through_while_closing_a_fence():
    # Signed off by the user: notes below PACT's block hold a Working Memory heading and then a copy of the
    # memory end marker line; telling PACT's real marker from the copy needs the unknown fence structure before.
    tail = "\n## Working Memory\n\nmy own\n\n```\n" + MEMORY_END_MARKER + "\n```\n"
    assert verdict(T13 + tail, sub(C13, pin(13), pin(13) + NEW + NEW2) + tail) == "ALLOW"


def test_lines_below_the_block_added_or_edited_with_a_pin_are_still_refused():
    assert verdict(T13, sub(C13, pin(13), pin(13) + NEW) + HEADING_BELOW) == "DENY"
    edited = LITERAL_BELOW.replace("## My notes", "## My notes, edited")
    assert verdict(T13 + LITERAL_BELOW, sub(C13, pin(13), pin(13) + NEW) + edited) == "DENY"


def test_working_memory_moved_below_the_managed_block_with_a_rename_is_allowed():
    moved = sub(sub(C13, "\n## Working Memory\n" + WM, "\n"), "### Pin 2\n", "### Pin 2 renamed\n")
    assert verdict(T13, moved + "\n## Working Memory\n" + WM) == "ALLOW"


def test_rows_run_in_the_unclosed_fence_state_too():
    # the same rename and the same addition, decided from a text whose Pinned section is past an unclosed fence
    assert verdict(T13, sub(C13, "### Pin 2\n", "### Pin 2 renamed\n")) == "ALLOW"
    assert verdict(T13, sub(C13, pin(13), pin(13) + NEW)) == "DENY"


# ------------------------------------------------------------------- counts

UNDATED = claude_md(pins(12) + pin(13, title="Undated", dated=False))


def test_growth_counts_each_added_pin_once():
    assert grow(B12, sub(B12, pin(12), pin(12) + NEW)) == 1
    assert grow(B12, sub(B12, pin(12), pin(12) + NEW + NEW2)) == 2


def test_an_undated_pin_renamed_moved_or_swapped_out_counts_no_growth():
    # no embedded-pin check runs where the rule counts: a pin's date comment never decides
    assert grow(UNDATED, sub(UNDATED, "### Undated\n", "### Undated renamed\n")) == 0
    assert grow(UNDATED, _move(UNDATED, pin(13, title="Undated", dated=False), pin(2))) == 0
    assert grow(UNDATED, sub(sub(UNDATED, pin(4), ""), pin(13, title="Undated", dated=False),
                             pin(13, title="Undated", dated=False) + "### Undated two\nx\n\n")) == 0


def test_a_prose_heading_smuggled_into_a_pin_body_is_counted_as_a_pin():
    assert verdict(B12, sub(B12, "body 7\n", "body 7\n### smuggled\nmore body\n")) == "DENY"


# ------------------------------------------------------------- replace_all

REPLACE_BEFORE = claude_md(pins(12, {3: "body 3\n```\nmarker line\n### step\n```\n", 10: "body 10\nmarker line\n"}))


def test_replace_all_every_site_is_denied_and_first_site_only_is_allowed():
    # old_string occurs first inside a snippet's fence, then in prose; new_string adds a ### line
    old, new = "marker line\n", "marker line\n### Added\n"
    assert REPLACE_BEFORE.count(old) == 2
    every_site = REPLACE_BEFORE.replace(old, new)
    first_site = REPLACE_BEFORE.replace(old, new, 1)
    assert verdict(REPLACE_BEFORE, every_site) == "DENY"
    assert verdict(REPLACE_BEFORE, first_site) == "ALLOW"


def _edit_for(before, after):
    """The smallest Edit turning `before` into `after`: the differing middle,
    widened a character at a time on both sides until old_string occurs once."""
    p = 0
    while p < min(len(before), len(after)) and before[p] == after[p]:
        p += 1
    s = 0
    while s < min(len(before), len(after)) - p and before[-1 - s] == after[-1 - s]:
        s += 1
    a, b, c, d = p, len(before) - s, p, len(after) - s
    while a == b or before.find(before[a:b]) != before.rfind(before[a:b]):
        if a > 0:
            a, c = a - 1, c - 1
        if b < len(before):
            b, d = b + 1, d + 1
    return before[a:b], after[c:d]


# A first Write has no text before, so it has no Edit form.
FORMS_ROWS = [(n, b, a, expected) for rows, expected in ((ALLOWED + RESIDUALS, "ALLOW"), (DENIED, "DENY"),
                                                          (NOT_FOUND, "ALLOW_ADVISORY"))
              for n, b, a in rows if b]


@pytest.mark.parametrize("name, before, after, expected", FORMS_ROWS, ids=[r[0] for r in FORMS_ROWS])
def test_every_row_gets_one_verdict_as_a_write_an_edit_and_a_replace_all_edit(name, before, after, expected):
    """The gate's own decision, which simulates the tool on the text before,
    decides each row the same way in all three forms."""
    from pin_caps_gate import gate_decision

    old, new = _edit_for(before, after)
    assert before.replace(old, new, 1) == after
    forms = [("Write", {"content": after}),
             ("Edit", {"old_string": old, "new_string": new, "replace_all": False}),
             ("Edit", {"old_string": old, "new_string": new, "replace_all": True})]
    assert [gate_decision(before, tool, tool_input).verdict for tool, tool_input in forms] == [expected] * 3


def test_the_gate_applies_every_replace_all_site_and_only_the_first_without_it():
    """old_string occurs twice, first in a snippet's fence, then in prose. Without
    replace_all the tool would refuse a non-unique old_string; the gate applies
    the first site and does not fail."""
    from pin_caps_gate import gate_decision

    edit = {"old_string": "marker line\n", "new_string": "marker line\n### Added\n"}
    assert gate_decision(REPLACE_BEFORE, "Edit", {**edit, "replace_all": True}).verdict == "DENY"
    assert gate_decision(REPLACE_BEFORE, "Edit", {**edit, "replace_all": False}).verdict == "ALLOW"


@pytest.mark.parametrize("before", [B13, T13], ids=["located", "past an unclosed fence"])
def test_an_edit_whose_literal_replace_changes_nothing_is_allowed(before):
    """The hook sees old_string before the tool normalises quotes, so its replace
    can miss: that is a plain allow, with no advisory even where the Pinned
    section cannot be located."""
    from pin_caps_gate import gate_decision

    missing = {"old_string": "\u2018not in the file\u2019", "new_string": "### A\n### B\n", "replace_all": False}
    assert missing["old_string"] not in before
    assert gate_decision(before, "Edit", missing).verdict == "ALLOW"
    assert gate_decision(before, "Edit", {**missing, "old_string": ""}).verdict == "ALLOW"


def test_a_change_that_leaves_the_text_unchanged_counts_no_growth():
    assert grow(B13, B13) == 0


# ---------------------------------------------------------------- the bound

def _alternating(lines, flip, repeat="x"):
    """A memory block where every other line repeats and the lines between
    differ between the two sides: the matcher's costly shape."""
    body = "".join((f"{repeat}\n" if k % 2 == 0 else f"line {k} {'b' if flip else 'a'}\n") for k in range(lines))
    return claude_md(pins(13) + "```\n" + body + "```\n\n")


class _SpyBudget(pin_growth._Budget):
    made = []

    def __init__(self, limit):
        super().__init__(limit)
        _SpyBudget.made.append(self)


def test_an_addition_that_fits_the_budget_is_decided():
    assert verdict(B13, sub(B13, pin(13), pin(13) + NEW)) == "DENY"


def test_the_alternating_shape_stops_within_one_row_of_the_budget(monkeypatch):
    _SpyBudget.made = []
    monkeypatch.setattr(pin_growth, "_Budget", _SpyBudget)
    before, after = _alternating(400, False), sub(_alternating(400, True), pin(13), pin(13) + NEW)
    with pytest.raises(pin_growth.SizeBound):
        pin_growth.pin_growth(parse(before), parse(after), budget=200_000)
    (spy,) = _SpyBudget.made
    repeats = after.count("\nx\n") + 1
    # one outer row spends one step plus one per occurrence of its line on the other side
    assert spy.limit < spy.spent <= spy.limit + 1 + repeats


def test_the_default_budget_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(pin_growth, "STEP_BUDGET", 10)
    after = sub(sub(B13, "body 2\n", "body two\n"), "body 12\n", "body twelve\n")  # an untrimmed middle of many rows
    with pytest.raises(pin_growth.SizeBound):
        pin_growth.pin_growth(parse(B13), parse(after))


def test_a_write_that_pads_a_short_block_into_the_costly_shape_is_still_decided(monkeypatch):
    # The costly shape needs repeats on BOTH sides. Padding a short block costs steps in proportion to its
    # length, so padding alone cannot exhaust the budget and buy an allow: the added pins are refused.
    _SpyBudget.made = []
    monkeypatch.setattr(pin_growth, "_Budget", _SpyBudget)
    padded = sub(_alternating(2000, True, repeat=""), pin(13), pin(13) + NEW + NEW2)
    assert verdict(claude_md(pins(13)), padded) == "DENY"
    (spy,) = _SpyBudget.made
    assert spy.spent < 100_000


def test_many_moved_snippets_exhaust_the_budget_in_the_moved_block_search(monkeypatch):
    blocks = [f"body {i}\n```\n### step {i}\ncommon\n```\n" for i in range(1, 41)]
    before = claude_md(pins(40, {i: blocks[i - 1] for i in range(1, 41)}))
    after = claude_md(pins(40, {i: blocks[40 - i] for i in range(1, 41)}))
    spent_aligning = []
    real_align = pin_growth._align

    def align(a, b, budget, trim, ranges):
        result = real_align(a, b, budget, trim, ranges)
        spent_aligning.append(budget.spent)
        return result

    monkeypatch.setattr(pin_growth, "_align", align)
    unbounded = pin_growth.pin_growth(parse(before), parse(after), budget=10 ** 9)
    assert unbounded is not None
    budget = spent_aligning[-1] + 50
    with pytest.raises(pin_growth.SizeBound) as raised:
        pin_growth.pin_growth(parse(before), parse(after), budget=budget)
    assert any(frame.name == "clause_moved_block" for frame in __import__("traceback").extract_tb(raised.tb))


def test_the_moved_block_run_check_spends_steps_for_each_compare():
    # Fenced blocks moved above their pins and edited where they stood: every candidate run is checked for being
    # still in place, one slice compare per aligned row. Those compares spend steps, so the budget bounds them.
    def block(i, head):
        return ["```"] + [f"line {i} {r}" for r in range(200)] + [head, "```"]
    before, after = ["### pin top"], ["### pin top"]
    for i in range(2):
        after += block(i, f"### h {i}")
    for i in range(2):
        before += [f"### pin {i}", "text"] + block(i, f"### h {i}")
        after += [f"### pin {i}", "text"] + block(i, f"### h {i} edited")
    wrap = "\n".join  # one memory block holding the Pinned section
    before_doc = parse(wrap([MEMORY_START_MARKER, "## Pinned Context", *before, MEMORY_END_MARKER, ""]))
    after_doc = parse(wrap([MEMORY_START_MARKER, "## Pinned Context", *after, MEMORY_END_MARKER, ""]))
    with pytest.raises(pin_growth.SizeBound) as raised:
        pin_growth.pin_growth(before_doc, after_doc, budget=40_000)  # about 5,000 steps without those compares
    assert any(frame.name == "_run_gone" for frame in __import__("traceback").extract_tb(raised.tb))


# ---------------------------------------------------------------- the timer

def _trip_after_first_alignment(monkeypatch):
    real_align = pin_growth._align

    def align(*args):
        result = real_align(*args)
        signal.raise_signal(signal.SIGALRM)
        return result

    monkeypatch.setattr(pin_growth, "_align", align)


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_the_timer_raises_size_bound_and_restores_the_previous_handler(monkeypatch):
    fired = []

    def sentinel(signum, frame):
        fired.append(signum)

    previous = signal.signal(signal.SIGALRM, sentinel)
    try:
        _trip_after_first_alignment(monkeypatch)
        with pytest.raises(pin_growth.SizeBound):
            pin_growth.run_with_timer(lambda: pin_growth.pin_growth(parse(B13), parse(sub(B13, "body 5\n", "b5\n"))))
        assert fired == []  # the rule's own handler was installed
        assert signal.getsignal(signal.SIGALRM) is sentinel
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    finally:
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_the_normal_path_restores_the_handler_and_disarms_the_timer():
    def sentinel(signum, frame):
        pass

    previous = signal.signal(signal.SIGALRM, sentinel)
    try:
        assert pin_growth.run_with_timer(lambda: 7) == 7
        assert signal.getsignal(signal.SIGALRM) is sentinel
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    finally:
        signal.signal(signal.SIGALRM, previous)


def test_without_setitimer_the_work_runs_without_a_timer(monkeypatch):
    monkeypatch.delattr(signal, "setitimer", raising=False)
    assert pin_growth.run_with_timer(lambda: "decided") == "decided"


def test_off_the_main_thread_the_work_runs_without_a_timer():
    out = []
    worker = threading.Thread(target=lambda: out.append(pin_growth.run_with_timer(lambda: "decided")))
    worker.start()
    worker.join()
    assert out == ["decided"]


def test_use_timer_false_runs_without_touching_signals(monkeypatch):
    monkeypatch.setattr(signal, "signal", lambda *a: pytest.fail("signal.signal called"))
    assert pin_growth.run_with_timer(lambda: 1, use_timer=False) == 1


# ------------------------------------------------------- matcher equivalence

def _pairs_of_texts():
    for _, before, after in ALLOWED + DENIED + RESIDUALS:
        yield [line.content for line in parse(before).lines], [line.content for line in parse(after).lines]
    rnd = random.Random(1967)
    for _ in range(400):
        alphabet = [f"l{k}" for k in range(rnd.randint(1, 6))]
        yield ([rnd.choice(alphabet) for _ in range(rnd.randint(0, 40))],
               [rnd.choice(alphabet) for _ in range(rnd.randint(0, 40))])


def test_counted_matcher_gives_the_stdlib_opcodes():
    for a, b in _pairs_of_texts():
        counted = pin_growth.CountedMatcher(a, b, pin_growth._Budget(10 ** 12)).get_opcodes()
        assert counted == difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes(), (a, b)


def test_counted_matcher_counts_one_step_per_outer_row_at_least():
    budget = pin_growth._Budget(10 ** 12)
    pin_growth.CountedMatcher(["a", "b", "c"], ["x", "y"], budget).get_matching_blocks()
    assert budget.spent == 3


# ------------------------------------------------------------- the decision

def decide(before, after):
    return pin_growth.pin_cap_decision(before, after, use_timer=False)


def long_body(i, chars):
    """A pin body of about `chars` characters in 79-character lines."""
    line = ("x" * 78 + "\n")
    return f"body {i}\n" + line * (chars // 79) + "y" * (chars % 79)


@pytest.mark.parametrize("name, before, after", ALLOWED + RESIDUALS, ids=[r[0] for r in ALLOWED + RESIDUALS])
def test_the_decision_allows_what_the_rule_allows(name, before, after):
    assert decide(before, after).verdict == "ALLOW"


@pytest.mark.parametrize("name, before, after", DENIED, ids=[r[0] for r in DENIED])
def test_the_decision_denies_on_count_what_the_rule_denies(name, before, after):
    decision = decide(before, after)
    assert (decision.verdict, decision.cause) == ("DENY", "count")
    assert decision.growth is not None and decision.pins_after == decision.pins_before + decision.growth


@pytest.mark.parametrize("name, before, after", NOT_FOUND, ids=[r[0] for r in NOT_FOUND])
def test_an_unlocatable_section_allows_with_the_not_found_advisory(name, before, after):
    decision = decide(before, after)
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "not_found")
    assert decision.reason


@pytest.mark.parametrize("name, before, after", [r for r in NOT_FOUND if "markers removed" not in r[0]],
                         ids=[r[0] for r in NOT_FOUND if "markers removed" not in r[0]])
def test_the_not_found_advisory_names_the_line(name, before, after):
    # With both memory markers gone nothing is uncertain, so no line is named.
    reason = decide(before, after).reason
    assert reason is not None and re.search(r"\blines? \d", reason)


def _prose(seed, size):
    """About `size` characters of varied words over several lines."""
    rnd, words, total = random.Random(seed), [], 0
    while total < size:
        word = rnd.choice("gate cap pin fence parser marker section writer reader".split()) + str(rnd.randrange(50))
        words.append(word)
        total += len(word) + 1
    return "\n".join(" ".join(words[k:k + 9]) for k in range(0, len(words), 9))


BIG = _prose(11, 1700)
# Pin 6's body holds a stray `## Notes`, so pin 7 below it, oversize with no
# override, is not in the located Pinned section before the change.
HIDDEN_BIG = claude_md(pins(10, {6: "body 6\n## Notes\nsome notes", 7: BIG}))


def test_revealing_an_oversize_pin_below_a_stray_heading_is_allowed_on_size():
    decision = decide(HIDDEN_BIG, sub(HIDDEN_BIG, "## Notes\n", "#### Notes\n"))
    assert (decision.verdict, decision.cause) == ("ALLOW", None)


def test_revealing_an_oversize_pin_and_growing_it_is_denied_on_size():
    grown = sub(sub(HIDDEN_BIG, "## Notes\n", "#### Notes\n"), BIG, BIG + "\n" + _prose(13, 100))
    decision = decide(HIDDEN_BIG, grown)
    assert (decision.verdict, decision.cause) == ("DENY", "size")


NO_PINNED_BLOCK = sub(claude_md(""), "## Pinned Context\n\n", "")
# No Pinned section anywhere after the change: no section in the memory block,
# and none outside it either.
NO_PINNED = [
    ("a memory block that holds no Pinned section", NO_PINNED_BLOCK, sub(NO_PINNED_BLOCK, "wm entry one\n", "wm entry 1\n")),
    ("a file with no memory block and no Pinned heading", "", "# User notes\n\nsome text\n"),
    ("a change that deletes the Pinned section", B12, sub(B12, "## Pinned Context\n\n" + pins(12, {3: SNIP}), "")),
]


@pytest.mark.parametrize("name, before, after", NO_PINNED, ids=[r[0] for r in NO_PINNED])
def test_no_pinned_section_anywhere_is_a_plain_allow(name, before, after):
    assert decide(before, after) == pin_growth.PinDecision("ALLOW", 0, 0, None, None, None)


def test_a_pinned_section_outside_the_memory_block_keeps_the_advisory():
    # The cap's own locator reads ABSENT without the memory block, but a reader
    # still finds the section, so the change is not a plain allow.
    decision = decide("", PLAIN_PINNED)
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "not_found")


def test_an_undated_pin_renamed_at_the_cap_is_allowed_and_a_smuggled_heading_is_denied_on_count():
    assert decide(UNDATED, sub(UNDATED, "### Undated\n", "### Undated renamed\n")).verdict == "ALLOW"
    smuggled = decide(B12, sub(B12, "body 7\n", "body 7\n### smuggled\nmore body\n"))
    assert (smuggled.verdict, smuggled.cause) == ("DENY", "count")


@pytest.mark.parametrize("head, struck", [("see ", 0), ("see <!-- pinned: 2026-04-11 --> ", 27)],
                         ids=["no close at all", "one closed comment first"])
def test_a_row_of_comment_openers_with_no_close_after_them_decides_at_once(head, struck):
    # 60 KB of pin-comment openers with no `-->` after them, mid-row so the row
    # opens no HTML block. The strike reads a row only up to its last `-->`, so
    # none of those openers is scanned.
    row = head + "<!-- pinned: " * 4_700
    before = claude_md(pins(5))
    started = time.perf_counter()
    decision = pin_growth.pin_cap_decision(before, sub(before, "body 3\n", f"body 3\n{row}\n"))
    assert time.perf_counter() - started < 1.0
    assert (decision.verdict, decision.cause) == ("DENY", "size")
    assert decision.reason is not None
    assert f"{len('body 3') + 1 + len(row.rstrip()) - struck} chars" in decision.reason


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_the_timer_covers_the_whole_decision(monkeypatch):
    # Past the timer outside the alignment: reading the pins sleeps past it.
    original = pin_growth.section_pins

    def slow(doc, located):
        time.sleep(2)
        return original(doc, located)

    monkeypatch.setattr(pin_growth, "TIMER_SECONDS", 0.2)
    monkeypatch.setattr(pin_growth, "section_pins", slow)
    decision = pin_growth.pin_cap_decision(B12, sub(B12, pin(12), pin(12) + NEW))
    assert decision == pin_growth.PinDecision(
        "ALLOW_ADVISORY", 0, 0, None, "size_bound",
        "The pin cap check stopped early and allowed this change: the pin cap check ran past 0.2 s.")
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_the_budget_allows_with_the_size_advisory(monkeypatch):
    monkeypatch.setattr(pin_growth, "STEP_BUDGET", 10)
    after = sub(sub(B13, "body 2\n", "body two\n"), "body 12\n", "body twelve\n")
    decision = decide(B13, sub(after, pin(13), pin(13) + NEW))
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound")


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_the_timer_allows_with_the_size_advisory_and_restores_the_handler(monkeypatch):
    fired = []

    def sentinel(signum, frame):
        fired.append(signum)

    previous = signal.signal(signal.SIGALRM, sentinel)
    try:
        _trip_after_first_alignment(monkeypatch)
        decision = pin_growth.pin_cap_decision(B13, sub(B13, pin(13), pin(13) + NEW))
        assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound")
        assert fired == []
        assert signal.getsignal(signal.SIGALRM) is sentinel
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    finally:
        signal.signal(signal.SIGALRM, previous)


def test_without_setitimer_or_off_the_main_thread_the_decision_is_unchanged(monkeypatch):
    added = sub(B13, pin(13), pin(13) + NEW)
    out = []
    worker = threading.Thread(target=lambda: out.append(pin_growth.pin_cap_decision(B13, added).verdict))
    worker.start()
    worker.join()
    assert out == ["DENY"]
    monkeypatch.delattr(signal, "setitimer", raising=False)
    assert pin_growth.pin_cap_decision(B13, added).verdict == "DENY"
    assert pin_growth.pin_cap_decision(B13, sub(B13, "### Pin 2\n", "### Pin 2 renamed\n")).verdict == "ALLOW"


def test_a_failure_inside_the_decision_allows_with_the_error_advisory(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pin_growth, "pin_growth", broken)
    decision = decide(B13, sub(B13, pin(13), pin(13) + NEW))
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "error")
    assert decision.reason is not None and "RuntimeError" in decision.reason


def test_a_non_text_input_allows_with_the_error_advisory():
    decision = pin_growth.pin_cap_decision(B13, None)  # pyright: ignore[reportArgumentType]
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "error")


def test_the_decision_on_replace_all_texts():
    old, new = "marker line\n", "marker line\n### Added\n"
    assert decide(REPLACE_BEFORE, REPLACE_BEFORE.replace(old, new)).verdict == "DENY"
    assert decide(REPLACE_BEFORE, REPLACE_BEFORE.replace(old, new, 1)).verdict == "ALLOW"


# size axis with a located section before: a code example counts toward a pin's body (signed off)
OVERSIZE = claude_md(pins(13, {9: long_body(9, 1700)}))


def test_an_unchanged_oversize_pin_does_not_refuse_a_rename_elsewhere():
    assert decide(OVERSIZE, sub(OVERSIZE, "### Pin 2\n", "### Pin 2 renamed\n")).verdict == "ALLOW"


def test_a_snippet_bearing_pin_growing_past_the_size_limit_is_denied_on_size():
    before = claude_md(pins(12, {9: long_body(9, 1300) + "\n```\n### step\n```"}))
    after = claude_md(pins(12, {9: long_body(9, 1600) + "\n```\n### step\n```"}))
    decision = decide(before, after)
    assert (decision.verdict, decision.cause) == ("DENY", "size")


# size axis with no located section before (the fence in pin 6 is unclosed), compared by stretches
def _transition(bodies, extra_after=None, count=13):
    before = claude_md(pins(count, {6: OPEN6, **bodies}))
    return before, {6: CLOSE6, **bodies}, extra_after


def test_closing_a_fence_while_trimming_an_oversize_pin_is_allowed():
    before = claude_md(pins(13, {6: OPEN6, 9: long_body(9, 1700)}))
    after = claude_md(pins(13, {6: CLOSE6, 9: long_body(9, 1650)}))
    assert decide(before, after).verdict == "ALLOW"


def test_closing_a_fence_while_trimming_an_oversize_snippet_bearing_pin_is_allowed():
    snippet = "\n```\n### step\n```"
    before = claude_md(pins(13, {6: OPEN6, 9: long_body(9, 1700) + snippet}))
    after = claude_md(pins(13, {6: CLOSE6, 9: long_body(9, 1650) + snippet}))
    assert decide(before, after).verdict == "ALLOW"


def test_closing_a_fence_while_a_pin_grows_past_the_size_limit_is_denied_on_size():
    before = claude_md(pins(13, {6: OPEN6, 9: long_body(9, 1400)}))
    after = claude_md(pins(13, {6: CLOSE6, 9: long_body(9, 1600)}))
    decision = decide(before, after)
    assert (decision.verdict, decision.cause) == ("DENY", "size")


def test_deleting_an_overridden_pin_beside_an_unchanged_oversize_one_while_closing_a_fence_is_allowed():
    # The deleted pin's rows fall in its neighbour's stretch; its override must not hide that neighbour's size.
    overridden = "<!-- pinned: 2026-09-08, pin-size-override: verbatim spec -->\n### Pin 8\n" + long_body(8, 2000)
    rest = {9: long_body(9, 1600), 12: "body 12\n```bash\necho hi"}
    before = claude_md(pins(7) + overridden + "\n\n" + pins(13, rest, start=9))
    after = claude_md(pins(7) + pins(13, {**rest, 12: "body 12\n```bash\necho hi\n```"}, start=9))
    assert decide(before, after).verdict == "ALLOW"


def test_signed_off_a_brand_new_oversize_pin_while_closing_a_fence_is_allowed():
    # Signed off by the user: with no located section before, a pin with no aligned row is left out of the
    # size comparison, so a brand-new oversize pin added in the same edit that closes the fence is allowed.
    before = claude_md(pins(11, {6: OPEN6}))
    after = claude_md(pins(11, {6: CLOSE6}) + pin(12, title="Brand new", body=long_body(12, 1600)))
    assert decide(before, after).verdict == "ALLOW"


def test_signed_off_removing_an_oversize_pins_override_while_closing_a_fence_is_allowed():
    # Signed off by the user (the same residual): pseudo-pins before carry no override, so a pin over the size
    # limit whose override is removed in the same edit that closes the fence compares with itself and is allowed.
    overridden = "<!-- pinned: 2026-09-09, pin-size-override: verbatim spec -->\n### Pin 9\n" + long_body(9, 1700)
    plain = "<!-- pinned: 2026-09-09 -->\n### Pin 9\n" + long_body(9, 1700)
    rest = {12: "body 12\n```bash\necho hi"}
    before = claude_md(pins(8) + overridden + "\n\n" + pins(13, rest, start=10))
    after = claude_md(pins(8) + plain + "\n\n" + pins(13, {12: "body 12\n```bash\necho hi\n```"}, start=10))
    assert decide(before, after).verdict == "ALLOW"


def test_a_first_write_with_an_oversize_pin_is_denied_on_size():
    decision = decide("", claude_md(pins(11) + pin(12, body=long_body(12, 1600))))
    assert (decision.verdict, decision.cause) == ("DENY", "size")


# --------------------------------------------------------------- the module

def test_the_module_imports_nothing_heavy_at_load():
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import shared; before = set(sys.modules); "
        "import shared.pin_growth; print(' '.join(sorted(set(sys.modules) - before)))"
    )
    result = subprocess.run([sys.executable, "-c", probe, str(HOOKS)], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert "shared.pin_growth" in loaded  # the probe saw the import
    assert not loaded & {"staleness", "dataclasses", "subprocess", "datetime"}, loaded


def test_clause_functions_are_the_eight_named_seams():
    names = {name for name in dir(pin_growth) if name.startswith("clause_")}
    assert names == {"clause_region_r", "clause_intact", "clause_refenced", "clause_guarded_pairing",
                     "clause_moved_block", "clause_edited_in_place", "clause_no_leaving_credit",
                     "clause_past_stray_heading"}
