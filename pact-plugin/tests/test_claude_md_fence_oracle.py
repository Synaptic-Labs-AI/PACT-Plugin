"""The finder (hooks/shared/claude_md_markers.py) against the naive oracle.

claude_md_fence_oracle.py is a second implementation of the fence grammar and
the marker states, written from the plan's text and the rulings on it. The
corpus's expected table (tests/fixtures/claude_md_corpus/expected.json) is a
third, written by hand. The finder must match the oracle row by row, block by
block, marker by marker, section by section and line lookup by line lookup, on
the corpus and on generated documents under random scopes; the oracle must match
the hand-written table. None of the three derives from another, so a mistake
shared by two of them still meets the third.
"""

import ast
import collections
import json
import pathlib
import random
import re

import pytest

import claude_md_fence_oracle as oracle
from shared import claude_md_manager as manager
from shared.claude_md_markers import parse

CORPUS = pathlib.Path(__file__).resolve().parent / "fixtures" / "claude_md_corpus"
EXPECTED = json.loads((CORPUS / "expected.json").read_text(encoding="utf-8"))
# The legacy kernel markers are locals of claude_md_manager._plan_kernel_strip, so
# they cannot be imported. The start is a prefix: it does not end in `-->`.
KERNEL_START, KERNEL_END = "<!-- PACT_START:", "<!-- PACT_END -->"
PAIRS = {
    "SESSION": (manager.SESSION_START_MARKER, manager.SESSION_END_MARKER),
    "MEMORY": (manager.MEMORY_START_MARKER, manager.MEMORY_END_MARKER),
    "MANAGED": (manager.MANAGED_START_MARKER, manager.MANAGED_END_MARKER),
    "PINNED": (manager.PINNED_START_MARKER, manager.PINNED_END_MARKER),
    "KERNEL": (KERNEL_START, KERNEL_END),
}
STOP_PREFIXES = tuple("<!-- " + p for p in manager.PACT_BOUNDARY_PREFIXES) + (
    "<!-- " + manager.SESSION_BOUNDARY_PREFIX,)
SINGLETONS = tuple(lit for pair in PAIRS.values() for lit in pair) + (
    manager.WORKING_MEMORY_COMMENT, STOP_PREFIXES[0])
HEADINGS = ("^## Working Memory", "^## Pinned Context", "^### ", r"^#{1,2}\s")
SECTIONS = {
    "PINNED": (r"^## Pinned Context\s*$", r"#{1,2}\s"),
    "WORKING": (r"^## Working Memory\s*$", r"#\s|##\s(?!Working Memory)|---"),
    "RETRIEVED": (r"^## Retrieved Context\s*$", r"#\s|##\s(?!Retrieved Context)|---"),
    "ENTRY": (r"^### ", None),
}
_KIND = {"P": oracle.PROSE, "F": oracle.FENCE, "C": oracle.CODE, "U": oracle.UNKNOWN}


def _corpus_text(name):
    return (CORPUS / f"{name}.md").read_bytes().decode("utf-8", errors=EXPECTED[name]["decode"])


def _expand(rle):
    return [_KIND[tok[0]] for tok in rle.split() for _ in range(int(tok[1:]))]


def _compile(pattern):
    return None if pattern is None else re.compile(pattern)


def _located(loc):
    return loc.state.value, tuple(loc.spans), loc.cause.value if loc.cause is not None else None


def _finder_view(text, scope=None):
    doc = parse(text)
    rows = [(ln.start, ln.end, ln.content, ln.kind.value, ln.in_html) for ln in doc.lines]
    cause = doc.boundary_cause.value if doc.boundary_cause is not None else None
    blocks = {n: _located(doc.find_block(s, e, scope)) for n, (s, e) in PAIRS.items()}
    markers = {lit: _located(doc.find_marker(lit, scope)) for lit in SINGLETONS}
    lines = {p: tuple(doc.find_lines(re.compile(p), scope)) for p in HEADINGS}
    sections = {(n, u): _located(doc.find_section(re.compile(h), _compile(t), scope,
                                                  stop_prefixes=STOP_PREFIXES, unique=u))
                for n, (h, t) in SECTIONS.items() for u in (False, True)}
    return rows, doc.boundary, cause, doc.scope_known(scope), blocks, markers, lines, sections


def _oracle_view(text, scope=None):
    scan = oracle.scan(text)
    blocks = {n: tuple(oracle.find_block(scan, s, e, scope)) for n, (s, e) in PAIRS.items()}
    markers = {lit: tuple(oracle.find_marker(scan, lit, scope)) for lit in SINGLETONS}
    lines = {p: oracle.find_lines(scan, re.compile(p), scope) for p in HEADINGS}
    sections = {(n, u): tuple(oracle.find_section(scan, re.compile(h), _compile(t), scope, STOP_PREFIXES, u))
                for n, (h, t) in SECTIONS.items() for u in (False, True)}
    return ([tuple(r) for r in scan.rows], scan.boundary, scan.cause, oracle.scope_known(scan, scope),
            blocks, markers, lines, sections)


def _scope(entry):
    return None if entry is None else tuple(entry)


def test_the_oracle_imports_nothing_but_the_standard_library():
    assert oracle.__file__ is not None
    tree = ast.parse(pathlib.Path(oracle.__file__).read_text(encoding="utf-8"))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0] if n.level == 0 else ".")
    assert imported <= {"re", "typing"}, imported


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_oracle_matches_the_hand_written_table(name):
    exp = EXPECTED[name]
    scan = oracle.scan(_corpus_text(name))
    assert ([r.kind for r in scan.rows], scan.boundary, scan.cause) == (
        _expand(exp["kinds"]), exp["boundary"], exp["boundary_cause"]), exp["note"]
    # A case with no html_rows key has no in_html row, so the key cannot be left off.
    assert [i for i, r in enumerate(scan.rows) if r.in_html] == exp.get("html_rows", []), exp["note"]
    for pair, want in exp["blocks"].items():
        got = tuple(oracle.find_block(scan, *PAIRS[pair]))
        assert got == (want["state"], tuple(tuple(s) for s in want.get("spans", [])), want.get("cause")), (
            pair, exp["note"])
    for pattern, rows_wanted in exp.get("headings", {}).items():
        assert list(oracle.find_lines(scan, re.compile(pattern))) == rows_wanted, (pattern, exp["note"])
    for want in exp.get("markers", []):
        got = tuple(oracle.find_marker(scan, want["literal"], _scope(want["scope"])))
        assert got == (want["state"], tuple(tuple(s) for s in want["spans"]), want["cause"]), (want, exp["note"])
    for want in exp.get("sections", []):
        got = tuple(oracle.find_section(scan, re.compile(want["heading"]), _compile(want["terminator"]),
                                        _scope(want["scope"]), want["stop_prefixes"], want["unique"]))
        assert got == (want["state"], tuple(tuple(s) for s in want["spans"]), want["cause"]), (want, exp["note"])


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_finder_matches_the_oracle_on_the_corpus(name):
    text = _corpus_text(name)
    assert _finder_view(text) == _oracle_view(text), EXPECTED[name]["note"]


@pytest.mark.parametrize("scope", [(-1, 0), (0, 99), (3, 1), (99, 98), (2, 0)])
def test_the_finder_and_the_oracle_refuse_the_same_bad_scopes(scope):
    text = "a\nb\nc\n"
    with pytest.raises(ValueError):
        parse(text).find_marker(manager.SESSION_START_MARKER, scope)
    with pytest.raises(ValueError):
        oracle.find_marker(oracle.scan(text), manager.SESSION_START_MARKER, scope)


@pytest.mark.parametrize("call", ["marker", "block", "stop prefix"])
def test_the_finder_and_the_oracle_refuse_a_literal_that_is_no_comment(call):
    doc, scan = parse("## Pinned Context\n"), oracle.scan("## Pinned Context\n")
    heading = re.compile(SECTIONS["PINNED"][0])
    calls = {
        "marker": (lambda: doc.find_marker("## Pinned"), lambda: oracle.find_marker(scan, "## Pinned")),
        "block": (lambda: doc.find_block("SESSION_START", manager.SESSION_END_MARKER),
                  lambda: oracle.find_block(scan, "SESSION_START", manager.SESSION_END_MARKER)),
        "stop prefix": (lambda: doc.find_section(heading, None, stop_prefixes=("PACT_MEMORY_",)),
                        lambda: oracle.find_section(scan, heading, None, stop_prefixes=("PACT_MEMORY_",))),
    }
    for fn in calls[call]:
        with pytest.raises(ValueError):
            fn()


_ROWS = (
    "text", "", "# Notes", "## Pinned Context", "## Pinned Context  ", "## Working Memory",
    "## Retrieved Context", "---", "### Pin A", "- item", "> quote",
    "1. step", "* * *", "    four spaces", "\ttab", "   three", "x y", "a\x0cb",
    "see the ## Working Memory section", "  ## Working Memory", "> ## Pinned Context",
    "<!-- SESSION_START -->", "<!-- SESSION_END -->", "   <!-- PACT_MEMORY_START -->",
    "<!-- PACT_MEMORY_END -->  ", "    <!-- SESSION_START -->", "> <!-- SESSION_END -->",
    "use `<!-- SESSION_START -->` here", "<!-- SESSION_END --> trailing", "``<!-- SESSION_START -->``",
    "\\`<!-- SESSION_START -->\\`", "\\\\`<!-- SESSION_END -->`", "\\``<!-- PACT_MEMORY_END -->`",
    "<!-- PACT_MEMORY_PINNED_START -->", "<!-- PACT_MEMORY_PINNED_END -->",
    "<!-- PACT_START: v3 -->", "<!-- PACT_START:v3.16 -->", "<!-- PACT_END -->", "  <!-- PACT_START: v2 -->",
    "see <!-- PACT_START: v3 --> here", "<!-- PACT_START: v3 --> words", "<!-- PACT_START: unclosed",
    "`<!-- PACT_START: v3 -->`", "<!-- PACT_START: </pre> -->", "<!-- PACT_MEMORY_X ?> -->",
    "<!-- PACT_ROUTING_A ]]> -->",
    "<!-- note", "-->", "<!-- one -->", "<!-->", "<!--->", "text <!-- mid -->", "<!-- quotes `<!--` x",
    "<?php", "?>", "<?>", "<!DOCTYPE x", "<!DOCTYPE html", ">", "<![CDATA[", "]]>", "<pre>", "</pre>",
    "<script type=x>", "</script>", "<prefix", "<div>", "</div>",
)
# Multi-row HTML blocks that hold a heading or a marker, closed in ways that hide
# their rows and in ways that do not, so in_html has work to do.
_HTML_UNITS = (
    ["<!--", "## Pinned Context", "-->"],
    ["<!-- old section", "## Working Memory", "### Pin B", "-->"],
    ["<!-- old", "## Pinned Context", "--> kept for reference"],
    ["<!-- TODO tidy", "## Working Memory", "flow: a --> b"],
    ["<pre>", "## Retrieved Context", "<!-- PACT_MEMORY_END -->", "</pre>"],
    ["<pre>", "## Retrieved Context", "old text</pre>"],
    ["<!DOCTYPE x", "### Pin C", ">"],
    ["<?php", "## Pinned Context", "?>"],
)


def _fence_row(rnd):
    indent = rnd.choice(["", "", "", " ", "  ", "   ", "    ", "\t"])
    run = rnd.choice("`~") * rnd.choice([3, 3, 3, 4, 5])
    tail = rnd.choice(["", "", "", "bash", " md", "a`b", " ", "\t", " text", " ", "inline```"])
    prefix = "" if rnd.random() < 0.85 else rnd.choice(
        ["- ", "-\t", "- \t", "* ", "1. ", "3) ", "> ", ">", "> \t", "> - ", ">> ", "-"])
    return prefix + indent + run + tail


def _unit(rnd):
    """One row, a fence row, a multi-row HTML block, a section, or a whole marker pair."""
    r = rnd.random()
    if r < 0.22:
        start, end = rnd.choice(list(PAIRS.values()) + [PAIRS["KERNEL"]])
        if start == KERNEL_START:
            start = rnd.choice(["<!-- PACT_START: v3 -->", "<!-- PACT_START:v3.16 -->", "<!-- PACT_START: x -->",
                                "<!-- PACT_START: x"])
        pair = [start, rnd.choice(_ROWS), end]
        shape = rnd.random()
        if shape < 0.15:
            return pair + pair  # two pairs
        if shape < 0.3:
            return [start] + pair  # a nested start
        return pair
    if r < 0.29:
        opener = _fence_row(rnd)
        run = opener.lstrip(" \t>-*+0123456789.)")[:3]
        return [opener, rnd.choice(_ROWS), run if run in ("```", "~~~") else "```"]
    if r < 0.37:
        return list(rnd.choice(_HTML_UNITS))
    if r < 0.45:
        heading = rnd.choice(["## Pinned Context", "## Working Memory", "## Retrieved Context", "### Pin A"])
        return rnd.choice([[heading, "### Pin B"], [heading, "entry", heading, rnd.choice(_ROWS)]])
    if r < 0.55:
        return [_fence_row(rnd)]
    return [rnd.choice(_ROWS)]


def _document(rnd):
    rows = [row for _ in range(rnd.randint(1, 14)) for row in _unit(rnd)]
    ends = [rnd.choice(["\n", "\n", "\n", "\r\n", "\r"]) for _ in rows]
    if rnd.random() < 0.2:
        ends[-1] = ""
    text = "".join(r + e for r, e in zip(rows, ends))
    return ("\ufeff" + text) if rnd.random() < 0.1 else text


def _random_scope(rnd, n):
    r = rnd.random()
    if r < 0.3 or n == 0:
        return None
    first = rnd.randint(0, n - 1)
    if r < 0.4:
        return (first, first - 1)  # empty
    return (first, rnd.randint(first, n - 1))


# Seed 2 draws more: documents an HTML block makes uncertain take their other
# results with them, and 1,000 draws leave two of the floors below short.
@pytest.mark.parametrize("seed, draws", [(1, 1000), (2, 1100)])
def test_the_finder_matches_the_oracle_on_generated_documents(seed, draws):
    rnd = random.Random(seed)
    seen = collections.Counter()
    for _ in range(draws):
        text = _document(rnd)
        scope = _random_scope(rnd, len(oracle.split_rows(text)))
        expected = _oracle_view(text, scope)
        assert _finder_view(text, scope) == expected, (repr(text), scope)
        rows, boundary, cause, _known, blocks, markers, _lines, sections = expected
        seen[cause] += 1
        seen["closed fence"] += oracle.FENCE in [r[3] for r in rows]
        seen["in_html row"] += any(r[4] for r in rows)
        seen["empty scope"] += scope is not None and scope[0] == scope[1] + 1
        if (cause == oracle.COMMENT_BOUNDARY and boundary is not None
                and not str(rows[boundary][2]).lstrip(" ").startswith("<!--")):
            seen["boundary from another HTML type"] += 1
        seen[("KERNEL", blocks["KERNEL"][0])] += 1
        for state, spans, block_cause in blocks.values():
            seen[state] += 1
            seen[(state, block_cause)] += 1
            seen["stray block with spans"] += block_cause == oracle.STRAY and bool(spans)
        for state, spans, marker_cause in markers.values():
            seen[("marker", state)] += 1
            seen["stray marker with spans"] += marker_cause == oracle.STRAY and bool(spans)
        for (name, unique), (state, _spans, section_cause) in sections.items():
            seen[("section", state)] += 1
            seen[("section", state, unique)] += 1
            seen[("section", section_cause)] += 1
    # The generator must reach every boundary cause, closed fences, every block,
    # marker and section state, in_html rows and empty scopes, or the comparison
    # above proves less than it says.
    for key in (None, oracle.UNCLOSED_FENCE, oracle.CONTAINER_FENCE, oracle.COMMENT_BOUNDARY,
                oracle.UNCLOSED_HTML, oracle.HTML_HIDES_FENCE, "closed fence",
                "in_html row", "empty scope", "boundary from another HTML type", ("KERNEL", oracle.FOUND),
                oracle.FOUND, oracle.ABSENT, oracle.DUPLICATE, oracle.UNKNOWN,
                (oracle.MALFORMED, oracle.STRAY), (oracle.MALFORMED, oracle.UNPAIRED),
                (oracle.MALFORMED, oracle.NESTED), "stray block with spans", "stray marker with spans",
                ("marker", oracle.FOUND), ("marker", oracle.ABSENT), ("marker", oracle.DUPLICATE),
                ("marker", oracle.UNKNOWN), ("marker", oracle.MALFORMED),
                ("section", oracle.FOUND), ("section", oracle.ABSENT), ("section", oracle.UNKNOWN),
                ("section", oracle.DUPLICATE, True), ("section", oracle.COMMENTED)):
        assert seen[key] >= 20, (key, seen)
    assert seen[("section", oracle.DUPLICATE, False)] == 0, "only unique=True may return DUPLICATE"


def test_the_finder_matches_the_oracle_on_seeds_hypothesis_draws():
    """The comparison above on generator seeds that hypothesis draws, beside the
    two fixed ones. It runs where hypothesis is installed and shows as a skip
    where it is not; the fixed-seed test runs everywhere. It imports hypothesis
    through importorskip, so the skip carries its reason and the type checker
    never needs hypothesis installed."""
    hypothesis = pytest.importorskip("hypothesis", reason="hypothesis not installed")
    strategies = pytest.importorskip("hypothesis.strategies", reason="hypothesis not installed")

    @hypothesis.settings(max_examples=50, deadline=None)
    @hypothesis.given(strategies.integers(min_value=3, max_value=2**32 - 1))
    def compare(seed):
        rnd = random.Random(seed)
        for _ in range(20):
            text = _document(rnd)
            scope = _random_scope(rnd, len(oracle.split_rows(text)))
            assert _finder_view(text, scope) == _oracle_view(text, scope), (seed, repr(text), scope)

    compare()
