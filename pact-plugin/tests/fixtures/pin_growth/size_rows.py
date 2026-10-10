"""Fixed rows for the per-pin size rule: changes to oversize pins, each with the
verdict the size check must give.

The certification populations never build an oversize pin, so these rows carry
the rule. They cover renames, rewrites, moves, splits, merges, copies,
replacements and lists, and paragraphs moved between two oversize pins. Texts
are built from seeded random words, the same on every interpreter. A row is
(name, text before, text after, verdict).
"""

import random
from typing import List, NamedTuple

M_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
M_END = "<!-- PACT_MANAGED_END -->"
MEM_START = "<!-- PACT_MEMORY_START -->"
MEM_END = "<!-- PACT_MEMORY_END -->"
RC = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
WM = ("<!-- Auto-managed by pact-memory skill. Full history searchable via pact-memory skill. "
      "Keyed by folder name, so another checkout with the same name shares this section. -->")
WMH = "## Working Memory"


class SizeRow(NamedTuple):
    name: str
    before: str
    after: str
    verdict: str  # "ALLOW" or "DENY"


def pin(n, body=None, date="2026-10-01", override=None):
    head = (f"<!-- pinned: {date}, pin-size-override: {override} -->" if override is not None
            else f"<!-- pinned: {date} -->")
    return f"{head}\n### Pin {n}\n{body if body is not None else f'Body of pin {n}.'}\n"


def doc(pins_text):
    return (
        f"{M_START}\n# PACT Framework and Managed Project Memory\n\n"
        "<!-- SESSION_START -->\n## Current Session\n- Resume: x\n<!-- SESSION_END -->\n\n"
        f"{MEM_START}\n## Retrieved Context\n{RC}\n\n## Pinned Context\n\n{pins_text}\n"
        f"{WMH}\n{WM}\n{MEM_END}\n\n{M_END}\n"
    )


def words(tag, n):
    """About n characters of one repeated word."""
    return ((tag + " ") * (n // (len(tag) + 1))).strip()


def lines_of(seed, n, vocab, per_line=9):
    """About n characters of seeded words from `vocab`, `per_line` to a line."""
    rnd = random.Random(seed)
    out, line, total = [], [], 0
    while total < n:
        word = rnd.choice(vocab) + str(rnd.randrange(50))
        line.append(word)
        total += len(word) + 1
        if len(line) >= per_line:
            out.append(" ".join(line))
            line = []
    if line:
        out.append(" ".join(line))
    return "\n".join(out)


def sentences(seed, n, vocab):
    """About n characters of seeded sentences, one to a line."""
    rnd = random.Random(seed)
    out, total = [], 0
    while total < n:
        sentence = " ".join(rnd.choice(vocab) for _ in range(rnd.randrange(6, 14))).capitalize() + "."
        out.append(sentence)
        total += len(sentence) + 1
    return "\n".join(out)


def edit(before, old, new):
    assert old in before, old[:80]
    return before.replace(old, new, 1)


def rest(first, last):
    return [pin(i) for i in range(first, last + 1)]


def _add(before, body, n=9, **kw):
    return edit(before, "\n" + WMH, "\n" + pin(n, body=body, **kw) + "\n" + WMH)


def _targets_and_controls(rows: List[SizeRow]) -> None:
    A, Bt, G = words("alpha", 1800), words("beta", 1600), words("gamma", 1700)
    B3 = doc("\n".join([pin(1, body=A), pin(2, body=Bt)] + rest(3, 5)))
    B1 = doc("\n".join([pin(1, body=Bt)] + rest(2, 5)))
    R = rows.append
    R(SizeRow("new 1700 pin without override while an 1800 pin exists", B3, _add(B3, G), "DENY"))
    R(SizeRow("grow the 1600 pin to 1700 while an 1800 pin exists", B3, edit(B3, Bt, words("beta", 1700)), "DENY"))
    small = doc("\n".join([pin(1, body=A), pin(2, body=words("beta", 1400))]))
    R(SizeRow("grow a 1400 pin to 1600 while an 1800 pin exists", small,
              edit(small, words("beta", 1400), words("beta", 1600)), "DENY"))
    R(SizeRow("new 1700 pin, no other oversize pin", B1, _add(B1, G), "DENY"))
    R(SizeRow("new 1700 pin with an override", B3, _add(B3, G, override="verbatim dispatch form"), "ALLOW"))
    R(SizeRow("rename the 1600 pin", B1, edit(B1, "### Pin 1\n", "### Pin one\n"), "ALLOW"))
    R(SizeRow("typo in the 1600 pin, same length", B1, edit(B1, "beta beta beta", "btea beta beta"), "ALLOW"))
    R(SizeRow("shrink the 1600 pin to 1550", B1, edit(B1, Bt, words("beta", 1550)), "ALLOW"))
    R(SizeRow("rename and shrink the 1600 pin to 1550", B1,
              edit(B1, "### Pin 1\n" + Bt, "### Pin one\n" + words("beta", 1550)), "ALLOW"))
    R(SizeRow("rename and typo the 1600 pin", B1,
              edit(B1, "### Pin 1\n" + Bt[:20], "### Pin one\nbtea" + Bt[4:20]), "ALLOW"))
    R(SizeRow("Write moving the 1600 pin to the end", B1, doc("\n".join(rest(2, 5) + [pin(1, body=Bt)])), "ALLOW"))
    R(SizeRow("Write moving and renaming the 1600 pin", B1, doc("\n".join(rest(2, 5) + [pin(11, body=Bt)])), "ALLOW"))
    R(SizeRow("Write moving and shrinking the 1600 pin to 1590", B1,
              doc("\n".join([pin(2), pin(3), pin(1, body=words("beta", 1590)), pin(4), pin(5)])), "ALLOW"))
    R(SizeRow("Write reversing all pins with two oversize", B3,
              doc("\n".join(reversed([pin(1, body=A), pin(2, body=Bt)] + rest(3, 5)))), "ALLOW"))
    swapped = B3.replace("### Pin 1\n", "### TMP\n").replace("### Pin 2\n", "### Pin 1\n").replace("### TMP\n", "### Pin 2\n")
    R(SizeRow("swap the 1800 and 1600 pins' headings (bodies stay)", B3, swapped, "ALLOW"))
    D34 = words("delta", 3400)
    B34 = doc("\n".join([pin(1, body=D34)] + rest(2, 5)))
    R(SizeRow("split a 3400 pin into two 1700 pins (both over)", B34,
              edit(B34, pin(1, body=D34), pin(1, body=words("delta", 1700)) + "\n" + pin(100, body=words("delta", 1700))),
              "ALLOW"))
    B18 = doc("\n".join([pin(1, body=A)] + rest(2, 5)))
    R(SizeRow("split an 1800 pin into two 900 pins", B18,
              edit(B18, pin(1, body=A), pin(1, body=words("alpha", 900)) + "\n" + pin(100, body=words("alpha", 900))),
              "ALLOW"))
    R(SizeRow("CRLF Write of a file with a 1600 pin", B1, B1.replace("\n", "\r\n"), "ALLOW"))
    R(SizeRow("trailing spaces on the 1600 pin", B1, edit(B1, Bt, Bt + "    "), "ALLOW"))
    R(SizeRow("typo in a different pin while the 1800 and 1600 pins exist", B3,
              edit(B3, "Body of pin 3.", "Body of pin three."), "ALLOW"))
    over = doc("\n".join([pin(1, body=G, override="verbatim dispatch form")] + rest(2, 5)))
    R(SizeRow("remove the override from a 1700 pin", over,
              edit(over, ", pin-size-override: verbatim dispatch form", ""), "DENY"))
    R(SizeRow("copy the 1600 pin as a new pin", B1, _add(B1, Bt, n=7), "DENY"))
    R(SizeRow("replace the 1800 pin with a new, different 1700 pin in one Edit", B18,
              edit(B18, pin(1, body=A), pin(50, body=G)), "ALLOW"))
    R(SizeRow("shrink the 1800 pin to 1000 and add a new 1700 pin in one Write", B18,
              doc("\n".join([pin(1, body=words("alpha", 1000))] + rest(2, 5) + [pin(50, body=G)])), "DENY"))


VOCAB = ("gate cap pin fence parser marker section writer reader alignment budget timer "
         "session memory block heading example oversize override rationale migration drift "
         "report baseline census oracle corpus seed population verdict growth reveal stray").split()


def _rewrites_splits_and_copies(rows: List[SizeRow]) -> None:
    def prose(seed, n):
        return lines_of(seed, n, VOCAB)

    R = rows.append
    A, B = prose(1, 1800), prose(2, 1600)
    base = doc("\n".join([pin(1, body=A), pin(2, body=B)] + rest(3, 5)))
    R(SizeRow("heavy rewrite in place, heading kept, same size", base, edit(base, A, prose(3, 1790)), "ALLOW"))
    R(SizeRow("rename and heavy rewrite in place, same size", base,
              edit(base, "### Pin 1\n" + A, "### Pin new\n" + prose(3, 1790)), "ALLOW"))
    R(SizeRow("rename and rewrite half, same size", base,
              edit(base, "### Pin 1\n" + A, "### Pin new\n" + A[:900] + prose(4, 880)), "ALLOW"))
    typos = "\n".join(line.replace("gate", "gtae") if i % 5 == 0 else line for i, line in enumerate(A.split("\n")))
    R(SizeRow("typo in every fifth line, rename", base, edit(base, "### Pin 1\n" + A, "### Pin x\n" + typos), "ALLOW"))
    R(SizeRow("reorder the lines of the 1800 pin", base, edit(base, A, "\n".join(reversed(A.split("\n")))), "ALLOW"))
    C = prose(5, 3400)
    c_lines = C.split("\n")
    half = len(c_lines) // 2
    big = doc("\n".join([pin(1, body=C)] + rest(2, 5)))
    first, second = "\n".join(c_lines[:half]), "\n".join(c_lines[half:])
    R(SizeRow("split 3400 into two halves (both over)", big,
              edit(big, pin(1, body=C), pin(1, body=first) + "\n" + pin(100, body=second)), "ALLOW"))
    R(SizeRow("split 3400 into halves, then grow one by 1000", big,
              edit(big, pin(1, body=C), pin(1, body=first) + "\n" + pin(100, body=second + "\n" + prose(6, 1000))),
              "DENY"))
    R(SizeRow("split 3400 into 3000 and 400", big,
              edit(big, pin(1, body=C), pin(1, body="\n".join(c_lines[:-3])) + "\n" + pin(100, body="\n".join(c_lines[-3:]))),
              "ALLOW"))
    a_lines = A.split("\n")
    R(SizeRow("move 1000 of the 1800 into a new pin plus 600 new text", base,
              edit(base, pin(1, body=A), pin(1, body="\n".join(a_lines[:len(a_lines) // 2])) + "\n"
                   + pin(100, body="\n".join(a_lines[len(a_lines) // 2:]) + "\n" + prose(7, 600))), "ALLOW"))
    R(SizeRow("merge the 1800 and the 1600 pins into one", base,
              edit(base, pin(1, body=A) + "\n" + pin(2, body=B), pin(1, body=A + "\n" + B)), "DENY"))
    small = doc("\n".join([pin(1, body=A), pin(2, body=prose(8, 900)), pin(3, body=prose(9, 900))] + rest(4, 5)))
    R(SizeRow("merge two 900 pins into an 1800 one, while an 1800 exists", small,
              edit(small, pin(2, body=prose(8, 900)) + "\n" + pin(3, body=prose(9, 900)),
                   pin(2, body=prose(8, 900) + "\n" + prose(9, 900))), "DENY"))
    R(SizeRow("copy the 1600 pin under the same heading", base, _add(base, B, n=2), "DENY"))
    R(SizeRow("copy the 1600 pin under a new heading", base, _add(base, B, n=7), "DENY"))
    R(SizeRow("replace the 1800 pin (heading too) with different 1700 text", base,
              edit(base, pin(1, body=A), pin(50, body=prose(10, 1700))), "ALLOW"))
    R(SizeRow("shrink the 1800 to 1000 and add a different 1700 pin", base,
              doc("\n".join([pin(1, body=A[:1000]), pin(2, body=B)] + rest(3, 5) + [pin(50, body=prose(10, 1700))])),
              "DENY"))
    R(SizeRow("Write reversing all pins", base,
              doc("\n".join(reversed([pin(1, body=A), pin(2, body=B)] + rest(3, 5)))), "ALLOW"))
    R(SizeRow("Write moving and renaming the second oversize pin", base,
              doc("\n".join([pin(1, body=A)] + rest(3, 5) + [pin(22, body=B)])), "ALLOW"))
    swapped = base.replace("### Pin 1\n", "### T\n").replace("### Pin 2\n", "### Pin 1\n").replace("### T\n", "### Pin 2\n")
    R(SizeRow("Write swapping the two oversize pins' headings", base, swapped, "ALLOW"))
    R(SizeRow("CRLF Write of a file with two oversize pins", base, base.replace("\n", "\r\n"), "ALLOW"))


V1 = ("the gate reads the file before the change and compares it with the file after the change so "
      "that a pin added past the cap is refused").split()
V2 = ("a hook checks each edit to the project memory file and refuses one that would push the number "
      "of pinned entries over twelve").split()
VA = "deploy staging release tag build artifact pipeline runner cache image registry rollout".split()
VB = "parser fence marker heading section block row kind prose code unknown boundary".split()


def _renames_lists_and_moves(rows: List[SizeRow]) -> None:
    R = rows.append
    orig, rewrite = sentences(1, 1800, V1), sentences(2, 1650, V2)
    base = doc("\n".join([pin(1, body=orig)] + rest(2, 5)))
    R(SizeRow("rename and reword the 1800 pin, shrink to about 1650", base,
              edit(base, "### Pin 1\n" + orig, "### Pin cap gate, explained\n" + rewrite), "ALLOW"))
    R(SizeRow("reword the 1800 pin and shrink it, heading kept", base, edit(base, orig, rewrite), "ALLOW"))
    R(SizeRow("heading gains a colon, reword and shrink", base,
              edit(base, "### Pin 1\n" + orig, "### Pin 1:\n" + rewrite), "ALLOW"))
    urls = "\n".join(f"- https://example.com/{random.Random(i).choice(['docs', 'api', 'guide', 'ref'])}/{i:04d}/"
                     f"{random.Random(i * 7).randrange(10 ** 8):08d}" for i in range(40))
    bu = doc("\n".join([pin(1, body=urls)] + rest(2, 5)))
    R(SizeRow("a list of links: sort the lines and rename", bu,
              edit(bu, "### Pin 1\n" + urls,
                   "### Reference links (sorted)\n" + "\n".join(sorted(urls.split("\n"), key=lambda line: line[::-1]))),
              "ALLOW"))
    R(SizeRow("a list of links: drop the bullets and rename", bu,
              edit(bu, "### Pin 1\n" + urls, "### Reference links\n" + urls.replace("- ", "")), "ALLOW"))
    table = "| step | what the gate does |\n|---|---|\n" + "\n".join(f"| {i} | {line} |" for i, line in enumerate(orig.split("\n")))
    R(SizeRow("prose turned into a longer table, renamed", base, edit(base, "### Pin 1\n" + orig, "### Gate steps\n" + table),
              "DENY"))
    o2 = sentences(3, 1600, V2)
    bm = doc("\n".join([pin(1, body=orig), pin(2, body=o2)] + rest(3, 5)))
    R(SizeRow("merge 1800 and 1600 into one 3400 (same text)", bm,
              edit(bm, pin(1, body=orig) + "\n" + pin(2, body=o2), pin(1, body=orig + "\n" + o2)), "DENY"))
    C = sentences(4, 3400, V1)
    cl = C.split("\n")
    h = len(cl) // 2
    bs = doc("\n".join([pin(1, body=C)] + rest(2, 5)))
    R(SizeRow("split 3400 in two with a one-line intro on each", bs,
              edit(bs, pin(1, body=C), pin(1, body="Part one of the notes.\n" + "\n".join(cl[:h])) + "\n"
                   + pin(100, body="Part two of the notes.\n" + "\n".join(cl[h:]))), "DENY"))
    R(SizeRow("split 3400 in two, nothing added", bs,
              edit(bs, pin(1, body=C), pin(1, body="\n".join(cl[:h])) + "\n" + pin(100, body="\n".join(cl[h:]))), "ALLOW"))
    bst = doc("\n".join([pin(1, body=orig), pin(2, body="Body of pin 2.")] + rest(3, 5)))
    copied = orig[: int(len(orig) * 0.6)]
    R(SizeRow("rename an oversize pin while a small pin copies 60% of its text (Write)", bst,
              doc("\n".join([pin(1, body=orig).replace("### Pin 1", "### Pin one"), pin(2, body="Body of pin 2.\n" + copied)]
                            + rest(3, 5))), "ALLOW"))
    R(SizeRow("a small pin copies 60% of an oversize pin's text (Write)", bst,
              doc("\n".join([pin(1, body=orig), pin(2, body="Body of pin 2.\n" + copied)] + rest(3, 5))), "ALLOW"))
    items = [f"run the {w} check before {v} the {u} step" for w, v, u in zip(
        random.Random(1).choices(["lint", "type", "unit", "seam", "perf"], k=40),
        random.Random(2).choices(["merging", "tagging", "pushing"], k=40),
        random.Random(3).choices(["release", "deploy", "review", "publish"], k=40))]
    dash = "\n".join("- " + s for s in items)
    star = "\n".join("* " + s for s in items)
    num = "\n".join(f"{i + 1}. " + s for i, s in enumerate(items))
    bd = doc("\n".join([pin(1, body=dash)] + rest(2, 5)))
    R(SizeRow("bullets changed from dashes to stars, renamed", bd, edit(bd, "### Pin 1\n" + dash, "### Release checklist\n" + star),
              "ALLOW"))
    R(SizeRow("bullets changed from dashes to stars, heading kept", bd, edit(bd, dash, star), "ALLOW"))
    bn = doc("\n".join([pin(1, body=num)] + rest(2, 5)))
    renumbered = "\n".join(f"{i + 1}. " + s for i, s in enumerate(items[:2] + items[3:]))
    R(SizeRow("numbered list: delete item 3 (renumbers) and rename", bn,
              edit(bn, "### Pin 1\n" + num, "### Release checklist\n" + renumbered), "ALLOW"))


def _moved_paragraphs(rows: List[SizeRow]) -> None:
    def para(seed, n, vocab):
        return sentences(seed, n, vocab)

    R = rows.append
    A, B_keep, moved = para(1, 1600, VA), para(2, 1700, VB), para(3, 300, VB)
    B = B_keep + "\n" + moved
    tail = rest(3, 5)
    pre = doc("\n".join([pin(1, body=A), pin(2, body=B)] + tail))

    def post(a_body, b_body=B_keep):
        return doc("\n".join([pin(1, body=a_body), pin(2, body=b_body)] + tail))

    R(SizeRow("move a 300-character paragraph from one oversize pin to another (Write)", pre, post(A + "\n" + moved), "ALLOW"))
    R(SizeRow("the same move as one Edit spanning both pins", pre,
              edit(pre, pin(1, body=A) + "\n" + pin(2, body=B), pin(1, body=A + "\n" + moved) + "\n" + pin(2, body=B_keep)),
              "ALLOW"))
    R(SizeRow("move the paragraph rewrapped onto one line", pre, post(A + "\n" + " ".join(moved.split("\n"))), "ALLOW"))
    R(SizeRow("move the paragraph with each line bulleted", pre,
              post(A + "\n" + "\n".join("- " + line for line in moved.split("\n"))), "DENY"))
    R(SizeRow("move the paragraph with one typo per moved line", pre,
              post(A + "\n" + "\n".join(line.replace("e", "3", 1) for line in moved.split("\n"))), "ALLOW"))
    renamed = doc("\n".join([pin(1, body=A + "\n" + moved).replace("### Pin 1", "### Deploy notes"),
                             pin(2, body=B_keep).replace("### Pin 2", "### Parser notes")] + tail))
    R(SizeRow("move the paragraph and rename both pins", pre, renamed, "ALLOW"))
    for label, new_vocab, gone_vocab in (("disjoint words", VA, VB), ("the same small vocabulary", VB, VB)):
        gone, new = para(30, 300, gone_vocab), para(31, 300, new_vocab)
        before = doc("\n".join([pin(1, body=A), pin(2, body=B_keep + "\n" + gone)] + tail))
        after = doc("\n".join([pin(1, body=A + "\n" + new), pin(2, body=B_keep)] + tail))
        R(SizeRow(f"grow one oversize pin with new text while another loses an unrelated paragraph ({label})",
                  before, after, "DENY"))
    before = doc("\n".join([pin(1, body=A), pin(2, body=B_keep + "\nTODO")] + tail))
    after = doc("\n".join([pin(1, body=A + "\nTODO"), pin(2, body=B_keep)] + tail))
    R(SizeRow("move a one-word line from one oversize pin to another", before, after, "ALLOW"))


def _thresholds_and_constructed(rows: List[SizeRow]) -> None:
    R = rows.append
    a = [f"a{k:04d}" for k in range(320)]  # distinct words, so every word pair is distinct
    z = [f"z{k:04d}" for k in range(320)]
    base = doc("\n".join([pin(1, body=" ".join(a))] + rest(2, 5)))
    for share, kept, new in (("51%", 154, 147), ("49%", 148, 153)):
        # Pin 1 stays as itself, shrunk, so it has a successor and cannot pair with
        # an orphan; a new oversize pin holds `kept` of its words from pin 1.
        after = doc("\n".join([pin(1, body=" ".join(a[:100])), pin(60, body=" ".join(a[:kept] + z[:new]))] + rest(2, 5)))
        R(SizeRow(f"a new oversize pin holding {share} of its word pairs from a kept pin",
                  base, after, "ALLOW" if share == "51%" else "DENY"))
    # An oversize pin rewritten in place under its own heading while 60% of its
    # old lines move into a small pin: the heading keeps the rewrite the same pin.
    old = lines_of(41, 1800, VOCAB)
    old_lines = old.split("\n")
    before = doc("\n".join([pin(1, body=old), pin(2, body="Body of pin 2.")] + rest(3, 5)))
    moved = "\n".join(old_lines[: int(len(old_lines) * 0.6)])
    after = doc("\n".join([pin(1, body=lines_of(42, 1700, VOCAB)), pin(2, body="Body of pin 2.\n" + moved)] + rest(3, 5)))
    R(SizeRow("rewrite an oversize pin in place while 60% of its old lines move into a small pin", before, after, "ALLOW"))
    # A pin that shares only part of an oversize pin's text joins it without
    # succeeding it, so that pin is not free to cover a new oversize pin too.
    long = [f"b{k:04d}" for k in range(300)]
    before = doc("\n".join([pin(1, body=" ".join(long))] + rest(2, 5)))
    after = doc("\n".join([pin(70, body=" ".join(long[:130] + z[:126])), pin(71, body=lines_of(43, 1700, VOCAB))]
                          + rest(2, 5)))
    R(SizeRow("rework an oversize pin into a new one and add a second new oversize pin", before, after, "DENY"))
    # A pin shrunk below the cap that keeps the start of its text, while a new
    # oversize pin copies that start and adds text: only the shared word pairs
    # join the new pin to the old one, and the oversize total falls.
    text = sentences(51, 1800, V1).split("\n")
    start = "\n".join(text[: int(len(text) * 0.55)])
    before = doc("\n".join([pin(1, body="\n".join(text))] + rest(2, 5)))
    after = doc("\n".join([pin(1, body=start), pin(80, body=start + "\n" + sentences(52, 600, V2))] + rest(2, 5)))
    R(SizeRow("shrink an oversize pin and copy what it kept into a new oversize pin with added text", before, after, "ALLOW"))
    # A paragraph moved from one oversize pin into the larger one, which then
    # grows past the largest pin before; the pair's total stays the same.
    big, small, moved = sentences(53, 2000, VA), sentences(54, 1600, VB), sentences(55, 300, VB)
    before = doc("\n".join([pin(1, body=big), pin(2, body=small + "\n" + moved)] + rest(3, 5)))
    after = doc("\n".join([pin(1, body=big + "\n" + moved), pin(2, body=small)] + rest(3, 5)))
    R(SizeRow("move a paragraph into the larger oversize pin, which grows past the largest before", before, after, "DENY"))
    # One oversize pin grows by new text while another loses a longer, unrelated
    # paragraph in the same small vocabulary: common word pairs must not join them.
    keep_a, keep_b = sentences(56, 1600, VB), sentences(57, 1700, VB)
    gone, added = sentences(58, 400, VB), sentences(59, 300, VB)
    before = doc("\n".join([pin(1, body=keep_a), pin(2, body=keep_b + "\n" + gone)] + rest(3, 5)))
    after = doc("\n".join([pin(1, body=keep_a + "\n" + added), pin(2, body=keep_b)] + rest(3, 5)))
    R(SizeRow("grow one oversize pin with new text while another loses a longer unrelated paragraph", before, after, "DENY"))


def build_size_rows() -> tuple:
    rows: List[SizeRow] = []
    _targets_and_controls(rows)
    _rewrites_splits_and_copies(rows)
    _renames_lists_and_moves(rows)
    _moved_paragraphs(rows)
    _thresholds_and_constructed(rows)
    names = [row.name for row in rows]
    assert len(names) == len(set(names)), "duplicate row names"
    return tuple(rows)


SIZE_ROWS = build_size_rows()
