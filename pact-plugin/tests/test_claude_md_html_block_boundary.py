"""
Location: pact-plugin/tests/test_claude_md_html_block_boundary.py
Summary: An HTML block that hides nothing (never closed, closed mid-line, or a
         declaration) makes the rest of the file uncertain when a row it covers
         would, read outside the block, open a code fence, a container fence or
         another HTML block. The generated documents and the sweep cannot build
         these shapes, so these fixed rows certify the rule.
Used by: pytest.

Rows: the boundary row and cause for every block type, closure and covered
row; the slice-start shape; the pin-cap gate on honest edits around such a
block (never a DENY of a faithful edit, still a DENY of growth in a certain
file); the Pinned readers' one path; the session writer and the per-launch
report naming the line and cause; and `parse` keeping no state between calls.
Every CLAUDE.md written here is under tmp_path.
"""
import ast
import pathlib

import pytest

import session_init
from pin_caps_gate import gate_decision
from shared import claude_md_markers
from shared.claude_md_markers import Cause, State, parse
from shared.session_resume import update_session_info
from staleness import locate_pinned

# --- the boundary row and cause --------------------------------------------

# Each block type's opener and end token, and two covered rows for it: one that
# starts another HTML block without closing it, and one that holds a whole
# one-row HTML block. Neither may hold the outer block's end token.
TYPES = {
    1: ("<pre>", "</pre>", "<?x", "<?x?>"),
    2: ("<!-- note", "-->", "<?x", "<?x?>"),
    3: ("<?php", "?>", "<pre>", "<pre>x</pre>"),
    4: ("<!DOCTYPE x", ">", "<![CDATA[", None),  # every one-row block holds a `>`
    5: ("<![CDATA[", "]]>", "<?x", "<?x?>"),
}
# Covered rows that change the parser's state when read outside the block.
STARTS = ("fence", "container fence", "block start")


def _covered(block_type, cover):
    return {"fence": "```", "container fence": "- ```", "block start": TYPES[block_type][2],
            "one-row block": TYPES[block_type][3], "neither": "## Working Memory"}[cover]


def _expected(block_type, closure, cover):
    """The ruled outcome: a never-closed block, a block closed mid-line, or a
    declaration closed anywhere, over a row that starts a structure."""
    if cover not in STARTS:
        return None, None
    if closure == "never":
        return 1, Cause.UNCLOSED_HTML
    if closure == "mid" or block_type == 4:
        return 1, Cause.HTML_HIDES_FENCE
    return None, None


CELLS = [
    (block_type, closure, cover)
    for block_type in TYPES
    for closure in ("never", "mid", "edge")
    for cover in ("fence", "container fence", "block start", "one-row block", "neither")
    if not (cover == "one-row block" and TYPES[block_type][3] is None)
]


@pytest.mark.parametrize("block_type, closure, cover", CELLS)
def test_the_boundary_row_and_cause(block_type, closure, cover):
    opener, end, _, _ = TYPES[block_type]
    closer = {"never": [], "mid": [f"a {end} b"], "edge": [end]}[closure]
    doc = parse("\n".join(["intro", opener, _covered(block_type, cover), *closer, "text"]) + "\n")
    assert (doc.boundary, doc.boundary_cause) == _expected(block_type, closure, cover)


@pytest.mark.parametrize("block_type", [1, 2, 3, 5])
def test_an_end_row_that_would_open_a_fence_counts_as_covered(block_type):
    # The block ends mid-line on a row that, read as prose, opens a fence.
    opener, end, _, _ = TYPES[block_type]
    doc = parse(f"intro\n{opener}\nplain\n``` {end} b\ntext\n")
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.HTML_HIDES_FENCE)


def test_a_block_closed_at_a_row_edge_still_hides_what_it_covers():
    doc = parse("intro\n<pre>\n```\n</pre>\n```\ncode\n```\n")
    assert doc.boundary is None
    assert [line.row for line in doc.lines if line.in_html] == [1, 2, 3]


@pytest.mark.parametrize("text, boundary", [
    ("- ```\nintro\n<?php\n```\n", (0, Cause.CONTAINER_FENCE)),  # a container fence above the block
    ("intro\n<pre>\n```\na </pre> b\n- ```\n", (1, Cause.HTML_HIDES_FENCE)),  # one below it
])
def test_the_earliest_boundary_wins(text, boundary):
    doc = parse(text)
    assert (doc.boundary, doc.boundary_cause) == boundary


def test_the_slice_start_shape_is_uncertain_at_the_opener():
    # A processing instruction opened in Retrieved Context and closed mid-line
    # in pin 5 covers a `<pre>` row in pin 3, so the whole file reads the
    # Pinned section as uncertain instead of reading it two ways.
    pinned = (_pins(10)
              .replace("Body of pin 3.\n", "Body of pin 3.\n<pre>\n")
              .replace("Body of pin 5.\n", "see ?> here\n")
              .replace("Body of pin 7.\n", "Body of pin 7.\n" + EXAMPLE))
    text = _doc(pinned, rc_body="<?xml version=1.0\n")
    doc = parse(text)
    opener_row = text.splitlines().index("<?xml version=1.0")
    assert opener_row == 11
    assert (doc.boundary, doc.boundary_cause) == (opener_row, Cause.HTML_HIDES_FENCE)
    assert locate_pinned(doc).state is State.UNKNOWN


# --- the pin-cap gate on honest edits --------------------------------------

EXAMPLE = "```md\n### a heading in an example\n```\n"
OPENERS = {"t1": ("<pre>", "</pre>"), "t3": ("<?xml version=1.0", "?>"), "t4": ("<!NOTE", ">"),
           "t5": ("<![CDATA[", "]]>")}
MEMORY_COMMENTS = (
    "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->",
    "<!-- Auto-managed by pact-memory skill. Full history searchable via pact-memory skill. "
    "Keyed by folder name, so another checkout with the same name shares this section. -->",
)


def _pin(n):
    return f"<!-- pinned: 2026-10-01 -->\n### Pin {n}\nBody of pin {n}.\n"


def _pins(n):
    return "\n".join(_pin(i) for i in range(1, n + 1))


def _doc(pinned, rc_body=""):
    return (
        "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
        "# PACT Framework and Managed Project Memory\n\n"
        "<!-- SESSION_START -->\n## Current Session\n- Resume: x\n<!-- SESSION_END -->\n\n"
        f"<!-- PACT_MEMORY_START -->\n## Retrieved Context\n{MEMORY_COMMENTS[0]}\n{rc_body}\n"
        f"## Pinned Context\n\n{pinned}\n## Working Memory\n{MEMORY_COMMENTS[1]}\n"
        "<!-- PACT_MEMORY_END -->\n\n<!-- PACT_MANAGED_END -->\n"
    )


def _file(count, opener, closer, where, shape):
    """`count` pins and an HTML block that hides nothing: opened in Retrieved
    Context or in pin 1, never closed or closed mid-line in pin 5, with a
    fenced example in pin 2 when the shape asks for one."""
    pinned = _pins(count).replace("Body of pin 2.\n", "Body of pin 2.\n" + (EXAMPLE if "ex" in shape else ""))
    body5 = f"see {closer} here\n" if shape.startswith("mid") else "Body of pin 5.\n"
    pinned = pinned.replace("Body of pin 5.\n", body5)
    if where == "pin1":
        pinned = pinned.replace("Body of pin 1.\n", f"Body of pin 1.\n{opener}\n")
        return _doc(pinned), body5
    return _doc(pinned, rc_body=f"{opener}\n"), body5


def _edits(before, opener, closer, body5, count):
    """(name, faithful, tool, tool_input): the honest fixes of the block and
    ordinary pin edits, then two that add a pin."""
    grow = count < 12
    add = "\n" + _pin(99) + "\n## Working Memory"
    return [
        ("delete the opener", True, "Edit", {"old_string": opener + "\n", "new_string": ""}),
        ("close it at a row edge", True, "Edit",
         {"old_string": opener + "\n", "new_string": f"{opener}\n{closer}\n"}),
        ("remove the mid-line closer", True, "Edit", {"old_string": body5, "new_string": "see here\n"}),
        ("rename pin 3", True, "Edit", {"old_string": "### Pin 3\n", "new_string": "### Pin three\n"}),
        ("delete pin 4", True, "Edit", {"old_string": _pin(4), "new_string": ""}),
        ("add a fenced example to pin 3", True, "Edit",
         {"old_string": "Body of pin 3.\n", "new_string": "Body of pin 3.\n" + EXAMPLE}),
        ("replace pin 4 with another", True, "Edit", {"old_string": _pin(4), "new_string": _pin(40)}),
        ("rewrite with CRLF", True, "Write", {"content": before.replace("\n", "\r\n")}),
        ("add a pin", grow, "Edit", {"old_string": "\n## Working Memory", "new_string": add}),
        ("delete the opener and add a pin", grow, "Write",
         {"content": before.replace(opener + "\n", "", 1).replace("\n## Working Memory", add, 1)}),
    ]


TRANSITIONS = [(count, name, where, shape)
               for count in (11, 12, 13) for name in OPENERS for where in ("rc", "pin1")
               for shape in ("never", "mid", "never+ex", "mid+ex")]


@pytest.mark.parametrize("count, opener_name, where, shape", TRANSITIONS)
def test_honest_edits_around_the_block(count, opener_name, where, shape):
    opener, closer = OPENERS[opener_name]
    before, body5 = _file(count, opener, closer, where, shape)
    for name, faithful, tool, tool_input in _edits(before, opener, closer, body5, count):
        decision = gate_decision(before, tool, tool_input)
        if faithful:
            assert decision.verdict != "DENY", (name, decision)
            continue
        after = tool_input["content"] if tool == "Write" else before.replace(
            tool_input["old_string"], tool_input["new_string"], 1)
        if parse(before).boundary is None and parse(after).boundary is None:
            assert decision.verdict == "DENY", (name, decision)


@pytest.mark.parametrize("opener_name", ["t1", "t3", "t5"])
def test_a_fenced_example_added_below_a_block_closed_mid_line_is_allowed_with_the_advisory(opener_name):
    # The block opens in Retrieved Context and closes mid-line in pin 5, so
    # the fenced example's `### ` line in pin 3 was counted as a 13th pin.
    opener, closer = OPENERS[opener_name]
    before, _ = _file(12, opener, closer, "rc", "mid")
    decision = gate_decision(before, "Edit", {"old_string": "Body of pin 3.\n",
                                              "new_string": "Body of pin 3.\n" + EXAMPLE})
    assert decision.verdict == "ALLOW_ADVISORY" and decision.cause == "not_found"


# --- the readers, the session writer and the per-launch report -------------

@pytest.mark.parametrize("opener_name", list(OPENERS))
@pytest.mark.parametrize("shape", ["never+ex", "mid+ex"])
def test_the_pinned_readers_see_no_section(opener_name, shape):
    opener, closer = OPENERS[opener_name]
    doc = parse(_file(12, opener, closer, "rc", shape)[0])
    assert doc.boundary is not None
    assert locate_pinned(doc).state is State.UNKNOWN
    assert locate_pinned(doc, unique=True).state is State.UNKNOWN


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.chdir(proj)
    return proj


# A never-closed processing instruction above everything, over a fence.
UNCERTAIN_FILE = "Notes\n<?xml version=1.0\n```\nexample\n```\n" + _doc(_pins(2))
UNCERTAIN_LINE = 2


def test_the_session_writer_refuses_naming_the_line_and_cause(project):
    target = project / ".claude" / "CLAUDE.md"
    target.write_bytes(UNCERTAIN_FILE.encode("utf-8"))
    status = update_session_info("s1", "team", started="2026-10-01 00:00:00 UTC")
    assert status is not None
    assert f"line {UNCERTAIN_LINE} starts an uncertain region: an HTML block is never closed" in status
    assert target.read_bytes().decode("utf-8") == UNCERTAIN_FILE


def test_the_per_launch_report_names_the_line_and_cause(project):
    (project / ".claude" / "CLAUDE.md").write_text(UNCERTAIN_FILE, encoding="utf-8")
    report = session_init.check_claude_md_refusals()
    assert report is not None
    assert f"line {UNCERTAIN_LINE} starts an uncertain region: an HTML block is never closed" in report


# --- parse keeps no state between calls ------------------------------------

def test_a_parse_reads_the_same_after_a_different_one():
    def view(doc):
        return [(line.kind, line.in_html) for line in doc.lines], doc.boundary, doc.boundary_cause

    texts = [UNCERTAIN_FILE, "intro\n<pre>\n## a\n", "<!DOCTYPE x\n```\n>\n", _doc(_pins(3))]
    first = [view(parse(text)) for text in texts]
    assert [view(parse(text)) for text in reversed(texts)] == list(reversed(first))


def test_the_parser_module_holds_no_state_a_parse_writes():
    # No function rebinds a module name, and none writes into a module-level
    # list, dict or set: every parse's state is local to the call.
    tree = ast.parse(pathlib.Path(claude_md_markers.__file__).read_text(encoding="utf-8"))
    module_names = {target.id for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    if isinstance(target, ast.Name)}
    mutators = {"append", "extend", "insert", "update", "add", "pop", "clear", "setdefault", "remove",
                "discard", "popitem", "sort", "reverse"}
    writes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            writes.append(ast.dump(node))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                if (isinstance(target, (ast.Subscript, ast.Attribute)) and isinstance(target.value, ast.Name)
                        and target.value.id in module_names):
                    writes.append(ast.dump(target))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in mutators and isinstance(node.func.value, ast.Name)
              and node.func.value.id in module_names):
            writes.append(ast.dump(node.func))
    assert writes == []
