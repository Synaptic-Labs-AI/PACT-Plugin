"""Mutation tests for the pin-growth rule (hooks/shared/pin_growth.py).

Each mutant replaces one module-level function of the rule, never source text, with
a variant that changes one thing (tests/fixtures/pin_growth/mutants.py). For each:

- the name must still exist in the module;
- the unmutated rule gives every red row its pinned verdict first (the control);
- with the replacement in place, every red row gets the opposite verdict as an
  ordinary decision: DENY for a denial reason, or a plain ALLOW, never an advisory,
  the size bound or an exception;
- the replacement ran on that row.

A meta-test fails when a clause function has no mutant, and the replacement bases
are checked against the functions they stand in for, so a kill comes from the one
change and not from a difference in the copy.
"""

import collections
import copy
from typing import Callable, NamedTuple, Tuple

import pytest

from shared import pin_growth

from fixtures.pin_growth import generators as G
from fixtures.pin_growth import harness as H
from fixtures.pin_growth import mutants as MU
from fixtures.pin_growth import rows as R

ROWS = {r.key: r for r in R.FIXED_ROWS + R.COMMENTED_ROWS + R.CONTENT_FENCE_ROWS + R.REMOVAL_ROWS + R.CEILING_ROWS}
CLAUSES = ("clause_region_r", "clause_intact", "clause_refenced", "clause_guarded_pairing",
           "clause_moved_block", "clause_edited_in_place", "clause_no_leaving_credit")
NOT_A_DECISION = ("size_bound", "not_found", "unreadable", "error")


class Mutant(NamedTuple):
    name: str
    replaces: str
    make: Callable          # the rule module -> the replacement function
    red: Tuple[str, ...]    # rows that must flip


def _locate(m):
    return m.locate_pinned


MUTANTS = (
    Mutant("raw growth only: every fenced ### line counts as a pin", "fenced_line_is_new",
           lambda m: MU.no_new_fenced_lines(),
           ("bodiless:close-and-add-a-fenced-snippet-holding-two-lines-to-p9",
            "bodiless:fence-pin-p10-into-an-example-and-add-p14",
            "bodiless:located-de-pin-p10-by-fencing-it-and-add-p14",
            "sections:located-copy-pin-3-s-fenced-snippet-byte-for-byte-into-pin-11")),
    Mutant("a credit for a ### line deleted from a fenced block that stays", "clause_no_leaving_credit",
           lambda m: MU.leaving_credit(_locate(m), kept=True),
           ("hidden:located-delete-hidden-p5-while-p6-stays-hidden-add-p17",
            "bodiless:located-rename-a-heading-inside-a-kept-snippet-13-pins",
            "dated:close-and-rename-pin-9-s-snippet-line-only",
            "sections:close-rename-pin-3-s-snippet-line-delete-pin-7-and-add-a-pin",
            "hidden:located-rename-a-snippet-line-only")),
    Mutant("the same credit without checking the block's opener", "clause_no_leaving_credit",
           lambda m: MU.leaving_credit(_locate(m), kept=True, opener_check=False),
           ("hidden:located-delete-hidden-p5-while-p6-stays-hidden-add-p17",
            "sections:close-delete-pin-10-s-heading-between-two-tilde-fences-and-add-a-pin")),
    Mutant("a credit for a fenced block that leaves the section", "clause_no_leaving_credit",
           lambda m: MU.leaving_credit(_locate(m), departure=True),
           ("hidden:located-move-hidden-p14-and-its-stray-pair-to-working-memory-add-p15",)),
    Mutant("pair every aligned line that was fenced before", "clause_guarded_pairing",
           lambda m: MU.guarded(always=True),
           ("hidden:a-hidden-pin-revealed-while-a-new-snippet-repeats-it",
            "snippets:close-a-fence-after-a-swallowed-pin-fencing-it-plus-a-pin-generated-layout",
            "snippets:a-hidden-pin-revealed-while-a-new-snippet-repeats-it-generated-layout")),
    Mutant("the guarded pairing off", "clause_guarded_pairing",
           lambda m: MU.guarded(always=False),
           ("hidden:located-a-stray-bearing-pin-moved-past-a-hidden-pin-plus-a-pin",
            "snippets:move-a-stray-bearing-pin-below-the-pins-it-hid-plus-a-pin")),
    Mutant("the guarded pairing without its fence clause", "clause_guarded_pairing",
           lambda m: MU.guarded(no_fence_clause=True),
           ("snippets:close-a-fence-after-a-swallowed-pin-fencing-it-plus-a-pin-generated-layout",)),
    Mutant("the guarded pairing without its prose-twin clause", "clause_guarded_pairing",
           lambda m: MU.guarded(no_twin_clause=True),
           ("snippets:move-a-stray-bearing-pin-below-the-pins-it-hid-plus-a-pin-repeating-the-still-hidden-pin-s-title",)),
    Mutant("R as the whole file", "clause_region_r",
           lambda m: MU.region(whole=True),
           ("dated:close-and-add-a-pin", "bodiless:close-and-add-p14")),
    Mutant("R ends at the first terminator line", "clause_region_r",
           lambda m: MU.region(first_end=True),
           ("dated:close-delete-a-working-memory-literal-from-pin-6-s-snippet-and-rename-pin-8",)),
    Mutant("the terminator search takes fenced literals", "clause_region_r",
           lambda m: MU.region(fenced_literals=True),
           ("literals:no-working-memory-a-retrieved-context-literal-in-pin-10-close-and-move-retrieved-context-below-pinned",
            "literals:a-retrieved-context-literal-in-pin-10-close-and-move-retrieved-context-below-pinned")),
    Mutant("no fallback end at a PACT section", "clause_region_r",
           lambda m: MU.region(no_section_fallback=True),
           ("sections:close-move-retrieved-context-below-pinned-and-add-a-pin",)),
    Mutant("a fallback end at any PACT-section line", "clause_region_r",
           lambda m: MU.region(any_section_fallback=True),
           ("literals:no-working-memory-a-retrieved-context-literal-in-pin-10-close-and-move-retrieved-context-below-pinned",
            "literals:no-working-memory-a-working-memory-literal-in-pin-10-close-and-move-retrieved-context-below-pinned",
            "literals:no-working-memory-close-move-retrieved-context-below-pinned-and-delete-pin-10-s-working-memory-literal")),
    Mutant("the moved-section end at the first moved heading", "clause_region_r",
           lambda m: MU.region(first_moved=True),
           ("sections:close-move-working-memory-above-pinned-and-delete-the-working-memory-literal-from-pin-6-s-snippet",)),
    Mutant("no moved-section check", "clause_region_r",
           lambda m: MU.region(no_moved=True),
           ("dated:close-add-a-pin-and-move-working-memory-above-pinned",)),
    Mutant("region R's start reads a commented-out heading", "clause_region_r",
           lambda m: MU.region(read_hidden=True),
           ("commented:close-and-add-a-pin", "commented:close-delete-the-commented-section-and-add-a-pin",
            "commented:close-convert-to-crlf-and-add-a-pin", "commented:three-old-lines-close-and-add-two-pins",
            "commented:bodiless-pins-close-and-add-p14")),
    Mutant("intact only by aligned fence partners", "clause_refenced",
           lambda m: MU.refenced(off=True),
           ("hidden:a-snippet-added-next-to-an-identical-fence-pair-plus-a-pin",
            "hidden:a-snippet-added-next-to-an-identical-fence-pair-a-rename-plus-a-pin",
            "snippets:re-fence-a-snippet-with-tildes-plus-a-pin",
            "snippets:re-fence-a-snippet-with-a-new-info-string-plus-a-pin",
            "snippets:re-fence-a-snippet-with-four-backticks-plus-a-pin")),
    Mutant("no re-fence equality", "clause_refenced",
           lambda m: MU.refenced(identical_only=True),
           ("snippets:re-fence-a-snippet-with-tildes-plus-a-pin",
            "snippets:re-fence-a-snippet-with-a-new-info-string-plus-a-pin",
            "snippets:re-fence-a-snippet-with-four-backticks-plus-a-pin")),
    Mutant("intact never: fence partners do not keep a line", "clause_intact",
           lambda m: MU.intact(always=False),
           ("content fence:generated-layout-a-snippet-line-beside-a-line-renamed-above-a-later-unclosed-fence-plus-a-pin",
            "content fence:generated-layout-a-snippet-line-between-lines-renamed-above-a-later-unclosed-fence-plus-a-pin")),
    Mutant("intact always: any aligned fenced line is kept", "clause_intact",
           lambda m: MU.intact(always=True),
           ("bodiless:fence-pin-p10-into-an-example-and-add-p14",
            "bodiless:located-de-pin-p10-by-fencing-it-and-add-p14",
            "sections:close-de-pin-pin-10-by-fencing-its-heading-and-body-add-a-pin")),
    Mutant("a moved block credited for its first ### line only", "clause_moved_block",
           lambda m: MU.moved(first_heading_only=True),
           ("snippets:move-a-snippet-holding-2-lines-plus-a-pin", "snippets:move-a-snippet-holding-3-lines-plus-a-pin",
            "snippets:a-nested-fence-line-pairs-a-moved-snippet-elsewhere-plus-a-pin")),
    Mutant("any aligned line between the fences keeps a run in place", "clause_moved_block",
           lambda m: MU.moved(inner_rows_only=True),
           ("snippets:swap-pins-2-and-4-across-a-longer-pin-snippets-sharing-a-shared-command-plus-a-pin",
            "snippets:swap-pins-2-and-4-across-a-longer-pin-snippets-sharing-a-blank-line-plus-a-pin",
            "snippets:a-nested-fence-line-pairs-a-moved-snippet-elsewhere-plus-a-pin")),
    Mutant("a run kept in place only by an identical block, ignoring an aligned ### line", "clause_moved_block",
           lambda m: MU.moved(ignore_aligned_heading=True),
           ("snippets:reveal-a-hidden-t4-while-a-new-snippet-repeats-it-byte-for-byte",)),
    Mutant("a moved block's fence lines must also be unaligned", "clause_moved_block",
           lambda m: MU.moved(fences_unaligned=True),
           ("hidden:a-moved-snippet-whose-fence-lines-pair-elsewhere-two-pins-added-one-moved-out",
            "hidden:a-moved-snippet-whose-fence-lines-pair-elsewhere-an-entry-moved-in",
            "hidden:bodiless-pins-a-moved-snippet-whose-fence-lines-pair-elsewhere-two-added-one-moved-out",
            "snippets:swap-pins-2-and-4-across-a-longer-pin-snippets-sharing-a-shared-command-plus-a-pin",
            "snippets:swap-pins-2-and-4-across-a-longer-pin-snippets-sharing-a-blank-line-plus-a-pin",
            "snippets:a-nested-fence-line-pairs-a-moved-snippet-elsewhere-plus-a-pin")),
    Mutant("no moved-block check", "clause_moved_block",
           lambda m: MU.moved(off=True),
           ("bodiless:close-move-p9-with-its-snippet-to-the-top-and-add-p14",
            "bodiless:located-move-p9-with-its-snippet-to-the-top-and-add-p13",
            "hidden:close-move-p9-with-its-snippet-to-the-end-and-add-p14",
            "hidden:a-moved-snippet-whose-fence-lines-pair-elsewhere-two-pins-added-one-moved-out",
            "hidden:a-moved-snippet-whose-fence-lines-pair-elsewhere-an-entry-moved-in",
            "hidden:bodiless-pins-a-moved-snippet-whose-fence-lines-pair-elsewhere-two-added-one-moved-out",
            "snippets:move-a-snippet-holding-2-lines-plus-a-pin",
            "snippets:move-a-snippet-holding-3-lines-plus-a-pin",
            "snippets:swap-pins-2-and-4-across-a-longer-pin-snippets-sharing-a-shared-command-plus-a-pin",
            "snippets:swap-pins-2-and-4-across-a-longer-pin-snippets-sharing-a-blank-line-plus-a-pin",
            "snippets:a-nested-fence-line-pairs-a-moved-snippet-elsewhere-plus-a-pin")),
    Mutant("no in-place pairing", "clause_edited_in_place",
           lambda m: MU.edited_in_place(off=True),
           ("dated:close-rename-pin-9-s-snippet-line-past-the-unclosed-fence-and-add-a-pin",
            "hidden:located-rename-a-snippet-line-and-add-p14",
            "hidden:rename-a-snippet-line-close-and-add-p14")),
    Mutant("the in-place span test reading uncertain rows by fence shape", "clause_edited_in_place",
           lambda m: MU.edited_in_place(unknown_by_shape=True),
           ("content fence:generated-layout-a-snippet-line-beside-a-line-renamed-above-a-later-unclosed-fence-plus-a-pin",
            "content fence:generated-layout-a-snippet-line-between-lines-renamed-above-a-later-unclosed-fence-plus-a-pin")),
    Mutant("the in-place span test on certain rows only", "clause_edited_in_place",
           lambda m: MU.edited_in_place(certain_only=True),
           ("hidden:rename-a-snippet-line-close-and-add-p14",
            "dated:close-rename-pin-9-s-snippet-line-past-the-unclosed-fence-and-add-a-pin")),
    Mutant("the in-place span test without code on certain rows", "clause_edited_in_place",
           lambda m: MU.edited_in_place(no_code_requirement=True),
           ("content fence:strays-removed-so-a-snippet-s-fences-paired-differently-before-nothing-fence-shaped-"
            "between-them-the-pin-between-deleted-a-snippet-line-added-plus-a-pin",)),
    Mutant("the in-place pairing with no span test", "clause_edited_in_place",
           lambda m: MU.edited_in_place(any_span=True),
           ("content fence:strays-removed-so-a-snippet-s-fences-paired-differently-before-nothing-fence-shaped-"
            "between-them-the-pin-between-deleted-a-snippet-line-added-plus-a-pin",
            "content fence:strays-removed-so-a-snippet-s-fences-paired-differently-before-a-block-between-them-the-pin-"
            "between-deleted-a-snippet-line-added-plus-a-pin")),
    Mutant("region R's start without the memory-block floor", "clause_region_r",
           lambda m: MU.region(no_floor=True),
           ("notes below:notes-above-with-a-pinned-context-heading-over-lines-close-and-add-a-pin",)),
    Mutant("region R's start floor with no fallback above PACT's block", "clause_region_r",
           lambda m: MU.region(floor_only=True),
           ("notes below:the-only-pinned-section-sits-above-pact-s-block-move-it-inside-and-rename-a-pin",)),
    Mutant("region R's end without the memory-block ceiling", "clause_region_r",
           lambda m: MU.region(no_ceiling=True),
           ("notes below:notes-below-with-a-real-working-memory-heading-close-and-add-a-pin",)),
    Mutant("the old net-growth rule", "pin_growth",
           lambda m: MU.net_growth(m.pin_growth, _locate(m)),
           ("bodiless:located-reveal-by-deleting-strays-and-add-a-fenced-snippet-with-a-line",)),
)


def _decide(row):
    return pin_growth.pin_cap_decision(row.pre if row.pre is not None else "", row.post, use_timer=False)


def _pinned(row):
    return "DENY" if row.label == R.GROWTH and not row.family else "ALLOW"


def _opposite_holds(decision, pinned):
    if pinned == "DENY":
        return decision.verdict == "ALLOW" and decision.cause is None
    return decision.verdict == "DENY" and decision.cause not in NOT_A_DECISION and decision.cause is not None


@pytest.mark.parametrize("mutant", MUTANTS, ids=[m.name for m in MUTANTS])
def test_each_mutant_flips_its_red_rows(mutant, monkeypatch):
    assert hasattr(pin_growth, mutant.replaces), f"{mutant.replaces} is gone from pin_growth"
    rows = [ROWS[k] for k in mutant.red]
    for row in rows:
        d = _decide(row)
        assert d.verdict == _pinned(row), f"control: {row.key} gave {d} before the mutation"
    replacement = mutant.make(pin_growth)
    ran = []

    def spy(*args, **kwargs):
        ran.append(current[0])
        return replacement(*args, **kwargs)

    current = [None]
    monkeypatch.setattr(pin_growth, mutant.replaces, spy)
    survivors = []
    for row in rows:
        current[0] = row.key
        d = _decide(row)
        if not _opposite_holds(d, _pinned(row)):
            survivors.append((row.key, d))
        assert row.key in ran, f"{mutant.replaces} replacement never ran on {row.key}"
    assert survivors == [], survivors


def test_every_clause_function_has_a_mutant():
    names = {n for n in dir(pin_growth) if n.startswith("clause_") and callable(getattr(pin_growth, n))}
    assert names == set(CLAUSES), names
    covered = {m.replaces for m in MUTANTS}
    assert set(CLAUSES) <= covered, set(CLAUSES) - covered


def test_every_red_row_exists_and_every_mutant_has_one():
    for m in MUTANTS:
        assert m.red, m.name
        missing = [k for k in m.red if k not in ROWS]
        assert not missing, (m.name, missing)


BASES = {
    "clause_region_r": lambda m: MU.region(),
    "clause_intact": lambda m: MU.intact(),
    "clause_refenced": lambda m: MU.refenced(),
    "clause_guarded_pairing": lambda m: MU.guarded(),
    "clause_moved_block": lambda m: MU.moved(),
    "clause_edited_in_place": lambda m: MU.edited_in_place(),
    "clause_no_leaving_credit": lambda m: MU.leaving_credit(_locate(m)),
}


def test_each_replacement_base_decides_as_the_clause_it_stands_in_for():
    items = (H.items_from_rows(R.FIXED_ROWS + R.COMMENTED_ROWS)
             + H.items_from_cases(G.transition_stream(1, 0.5, 300))
             + H.items_from_cases(G.layout_stream(1, 300))
             + H.items_from_cases(G.commented_stream(G.layout_stream(1, 150))))
    calls, mismatches = collections.Counter(), collections.defaultdict(list)
    current = [None]

    def compare(name, original, base):
        def wrapper(*args):
            claims = [a for a in args if isinstance(a, pin_growth.Claims)]
            before = copy.deepcopy(claims[0]) if claims else None
            result = original(*args)
            if claims:
                after = copy.deepcopy(claims[0])
                mine = base(*tuple(copy.deepcopy(before) if isinstance(a, pin_growth.Claims) else a for a in args))
                same = mine == result
                probe = copy.deepcopy(before)
                base(*tuple(probe if isinstance(a, pin_growth.Claims) else a for a in args))
                same = same and probe == after
            else:
                same = base(*args) == result
            calls[name] += 1
            if not same:
                mismatches[name].append(current[0])
            return result
        return wrapper

    with pytest.MonkeyPatch.context() as mp:
        for name, make in BASES.items():
            mp.setattr(pin_growth, name, compare(name, getattr(pin_growth, name), make(pin_growth)))
        for it in items:
            current[0] = it.key
            pin_growth.pin_cap_decision(it.pre if it.pre is not None else "", it.post, use_timer=False)
    assert all(calls[n] for n in BASES), calls
    assert not mismatches, {n: v[:5] for n, v in mismatches.items()}


def _run_check_shape(k, m):
    """K pins, each holding a fenced m-line block whose ### line the change edits in
    place, while every original block is copied, unedited, into the first pin."""
    def block(i, head):
        return ["```"] + [f"line {i} {r}" for r in range(m)] + [head, "```"]
    pre, post = ["### pin top", "<!-- pinned: 2026-01-01 -->"], ["### pin top", "<!-- pinned: 2026-01-01 -->"]
    for i in range(k):
        post += block(i, f"### h {i}")
    for i in range(k):
        pre += [f"### pin {i}", "<!-- pinned: 2026-01-01 -->", "text"] + block(i, f"### h {i}")
        post += [f"### pin {i}", "<!-- pinned: 2026-01-01 -->", "text"] + block(i, f"### h {i} edited")

    def text(body):
        return "\n".join(["<!-- PACT_MEMORY_START -->", "## Pinned Context", *body, "<!-- PACT_MEMORY_END -->", ""])
    from shared.claude_md_markers import parse
    return parse(text(pre)), parse(text(post))


def test_the_moved_block_search_charges_one_step_per_line_its_run_check_compares(monkeypatch):
    spent = []

    class Recording(pin_growth._Budget):
        def __init__(self, limit):
            super().__init__(limit)
            spent.append(self)

    monkeypatch.setattr(pin_growth, "_Budget", Recording)
    pre, post = _run_check_shape(40, 40)
    pin_growth.pin_growth(pre, post, budget=10 ** 12)
    charged = spent[-1].spent
    compared = [0]

    def uncharged_run_check(t, start, size):
        run = t.a[start:start + size]
        for k in range(start, start + size):
            q = t.p2q.get(k)
            if q is None:
                continue
            if t.pre.lines[k].content.startswith("### "):
                return False
            b0 = q - (k - start)
            if 0 <= b0 and b0 + size <= len(t.b):
                compared[0] += size
                if t.b[b0:b0 + size] == run:
                    return False
        return True

    monkeypatch.setattr(pin_growth, "_run_gone", uncharged_run_check)
    pin_growth.pin_growth(pre, post, budget=10 ** 12)
    uncharged = spent[-1].spent
    assert compared[0] > 0, "the shape never reached the run check's compare"
    assert charged == uncharged + compared[0], (charged, uncharged, compared[0])
    monkeypatch.undo()
    with pytest.raises(pin_growth.SizeBound):
        pin_growth.pin_growth(pre, post, budget=charged - 1)
