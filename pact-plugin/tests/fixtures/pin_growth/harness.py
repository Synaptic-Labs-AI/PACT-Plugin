"""Run populations of labelled changes through the pin-cap decision and tally the result.

The decision is the shipped one, pin_growth.pin_cap_decision: every axis counts. A
faithful change it denies is an over-block whatever the cause (count, size, embedded
pin, override). A growth change it allows while the oracle counts more than 12 pins
after the change is an under-block, and must belong to a signed-off family.
"""

import collections
from typing import Callable, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Tuple

from fixtures.pin_growth.generators import CAP, Case, classify, oracle_pin_count
from fixtures.pin_growth.rows import FAITHFUL, FAMILY_NOT_LOCATED, GROWTH, Row


class Item(NamedTuple):
    key: str
    pre: Optional[str]
    post: str
    label: str
    families: FrozenSet[str]
    kinds: Tuple[str, ...]


def items_from_rows(rows: Iterable[Row]) -> List[Item]:
    return [Item(r.key, r.pre, r.post, r.label, frozenset([r.family]) if r.family else frozenset(), ())
            for r in rows]


def items_from_cases(cases: Iterable[Case]) -> List[Item]:
    return [Item(f"{c.stream}#{c.index}", c.pre, c.post, c.label, classify(c), c.kinds) for c in cases]


def shipped_decide(pre: Optional[str], post: str):
    from shared import pin_growth
    return pin_growth.pin_cap_decision(pre if pre is not None else "", post, use_timer=False)


class Tally(NamedTuple):
    total: int
    counts: collections.Counter          # label, engagement and verdict counts
    over: List[Tuple[str, str]]          # (key, verdict and cause)
    under: List[Tuple[str, FrozenSet[str]]]
    unclassified: List[str]              # under-blocks with no signed-off family
    not_located: List[str]               # growth the gate allowed because nothing can be located
    divergent: List[Tuple[str, str]]     # the gate and the oracle disagree on locating the section
    size_bound: List[str]
    decisions: Dict[str, Tuple[str, Optional[str]]]

    def summary(self) -> str:
        fam = collections.Counter(f for _, fs in self.under for f in fs)
        return (f"n={self.total} {dict(sorted(self.counts.items()))} over={len(self.over)} "
                f"under={len(self.under)} unclassified={len(self.unclassified)} "
                f"not_located={len(self.not_located)} divergent={len(self.divergent)} "
                f"size_bound={len(self.size_bound)} families={dict(sorted(fam.items()))}")


def _no_pinned_section(text: str) -> bool:
    """True when neither the unique nor the reader's Pinned locator finds a
    section in `text`: both read ABSENT."""
    from shared.claude_md_markers import State, parse
    from staleness import locate_pinned

    doc = parse(text)
    return all(locate_pinned(doc, unique=unique).state is State.ABSENT for unique in (True, False))


def evaluate(items: Iterable[Item], decide: Callable = shipped_decide) -> Tally:
    counts = collections.Counter()
    over, under, unclassified, not_located, divergent, size_bound = [], [], [], [], [], []
    decisions = {}
    total = 0
    for it in items:
        total += 1
        d = decide(it.pre, it.post)
        verdict, cause = d.verdict, d.cause
        decisions[it.key] = (verdict, cause)
        shown = f"{verdict}({cause})"
        n = oracle_pin_count(it.post)
        counts[it.label] += 1
        counts[verdict] += 1
        if cause == "size_bound":
            size_bound.append(it.key)
        if n is None:
            counts["not located"] += 1
            # With no Pinned section anywhere the decision is a plain allow;
            # with one PACT cannot read, the not-found advisory.
            expected = ("ALLOW", None) if _no_pinned_section(it.post) else ("ALLOW_ADVISORY", "not_found")
            if (verdict, cause) != expected:
                divergent.append((it.key, shown))
            if verdict == "DENY":
                over.append((it.key, shown))
            elif it.label == GROWTH:
                not_located.append(it.key)
            continue
        if cause == "not_found":
            divergent.append((it.key, shown))
        engaged = n > CAP
        if it.label == FAITHFUL or not engaged:
            if verdict == "DENY":
                over.append((it.key, shown))
            continue
        counts["engaged growth"] += 1
        if verdict != "DENY":
            fams = it.families - {FAMILY_NOT_LOCATED}
            under.append((it.key, fams))
            if not fams:
                unclassified.append(it.key)
    return Tally(total, counts, over, under, unclassified, not_located, divergent, size_bound, decisions)


def first(iterable, n):
    out = []
    for x in iterable:
        if len(out) == n:
            break
        out.append(x)
    return out
