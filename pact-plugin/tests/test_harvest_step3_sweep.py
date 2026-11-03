"""Fail when a rule in the harvest Step 3 block has no test that catches it.

Every mutant of three small kinds is generated from the block's AST and run
against EVERY arm of `test_harvest_step3_exec.py`:

- a call argument (positional or keyword) replaced by `None`;
- a single-operator comparison negated;
- an `and`/`or` swapped.

A mutant is killed when any arm raises. The survivors must equal the
`EQUIVALENT` allowlist EXACTLY: a new survivor is a rule no arm pins, and an
allowlist entry that is no longer generated, or no longer survives, is stale.
A new arm joins the sweep automatically.

Mutant keys carry the enclosing function or block section, the expression and
the operator detail, and a counter for identical keys in that scope, never a
line number.
"""

import ast
import copy
import functools
import re
from collections import Counter

import test_harvest_step3_exec as step3

_NEGATE = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
}
_SECTION = re.compile(r"^\s*# ([A-Z][A-Z ]+?)[:.]")

# Survivors that change nothing observable, each with the reason.
EQUIVALENT = {
    "arg|DISCOVERY|e.get('agent', '')|''|1":
        "mixed_old_format_event treats a non-str agent as the empty owner",
    "arg|DISCOVERY|e.get('task_subject', '')|''|1":
        "disk_identity applies emit_side_subject: None and '' both become the sentinel",
    "arg|FROM SECTIONS|from_task.get('subject', '')|''|1":
        "disk_identity applies emit_side_subject: None and '' both become the sentinel",
    "arg|THIS SECTION|task.get('subject', '')|''|1":
        "disk_identity applies emit_side_subject: None and '' both become the sentinel",
    "arg|FROM SECTIONS|from_task.get('owner', '')|''|1":
        "disk_identity treats a non-str owner as the empty owner",
    "arg|THIS SECTION|task.get('owner', '')|''|1":
        "disk_identity treats a non-str owner as the empty owner",
    "arg|FROM SECTIONS|from_events.get(team, [])|[]|1":
        "group_events iterates `events or ()`: None and [] both yield no group",
}


def _sections(block: str) -> list:
    """(first line, name) for each upper-case section comment in the block."""
    out = []
    for number, line in enumerate(block.splitlines(), start=1):
        match = _SECTION.match(line)
        if match:
            out.append((number, match.group(1).strip()))
    return out


def _scope(node, parents, sections) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.FunctionDef):
            return current.name
    name = "top"
    for first, section in sections:
        if first <= node.lineno:
            name = section
    return name


@functools.lru_cache(maxsize=None)
def mutants(block: str = None):
    """[(key, mutated tree)] for every mutant of the block. Cached: the trees
    are compiled, never mutated, by the callers."""
    block = step3.BLOCK if block is None else block
    tree = ast.parse(block)
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    sections = _sections(block)
    nodes = list(ast.walk(tree))
    seen = Counter()
    out = []

    def add(base, index, edit):
        seen[base] += 1
        out.append((f"{base}|{seen[base]}", index, edit))

    for index, node in enumerate(nodes):
        if not isinstance(node, (ast.Call, ast.Compare, ast.BoolOp)):
            continue
        where = _scope(node, parents, sections)
        if isinstance(node, ast.Call):
            text = ast.unparse(node)
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Constant) and arg.value is None:
                    continue
                add(f"arg|{where}|{text}|{ast.unparse(arg)}", index, ("arg", i))
            for i, keyword in enumerate(node.keywords):
                value = keyword.value
                if isinstance(value, ast.Constant) and value.value is None:
                    continue
                add(f"kw|{where}|{text}|{keyword.arg}", index, ("kw", i))
        elif isinstance(node, ast.Compare) and len(node.ops) == 1:
            add(f"cmp|{where}|{ast.unparse(node)}|{type(node.ops[0]).__name__}",
                index, ("cmp", 0))
        elif isinstance(node, ast.BoolOp):
            add(f"bool|{where}|{ast.unparse(node)}|{type(node.op).__name__}",
                index, ("bool", 0))
    result = []
    for key, index, (kind, i) in out:
        mutated = copy.deepcopy(tree)
        target = list(ast.walk(mutated))[index]
        if kind == "arg":
            target.args[i] = ast.Constant(value=None)
        elif kind == "kw":
            target.keywords[i].value = ast.Constant(value=None)
        elif kind == "cmp":
            target.ops[0] = _NEGATE[type(target.ops[0])]()
        else:
            target.op = ast.Or() if isinstance(target.op, ast.And) else ast.And()
        result.append((key, ast.fix_missing_locations(mutated)))
    return result


def arms():
    """[(name, zero-argument callable)] for every arm, parameters expanded."""
    out = []
    for name, fn in vars(step3).items():
        if not name.startswith("test_") or not callable(fn):
            continue
        marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
        if not marks:
            out.append((name, fn))
            continue
        argname, values = marks[0].args[0], marks[0].args[1]
        for value in values:
            out.append((f"{name}[{value!r}]", lambda fn=fn, a=argname, v=value: fn(**{a: v})))
    return out


def _killed(tree, arm_list) -> bool:
    try:
        code = compile(tree, "SKILL.md#step3", "exec")
    except Exception:
        return True
    original = step3.CODE
    step3.CODE = code
    try:
        for _, arm in arm_list:
            try:
                arm()
            except Exception:
                return True
        return False
    finally:
        step3.CODE = original


def survivors(arm_list=None) -> set:
    arm_list = arms() if arm_list is None else arm_list
    return {key for key, tree in mutants() if not _killed(tree, arm_list)}


def test_the_unmutated_block_passes_every_arm():
    assert not _killed(ast.parse(step3.BLOCK), arms())


def test_every_call_in_the_block_yields_a_mutant():
    calls = [n for n in ast.walk(ast.parse(step3.BLOCK)) if isinstance(n, ast.Call)
             and any(not (isinstance(a, ast.Constant) and a.value is None)
                     for a in [*n.args, *(k.value for k in n.keywords)])]
    arg_mutants = [k for k, _ in mutants() if k.startswith(("arg|", "kw|"))]
    assert len(arg_mutants) >= len(calls) > 0


def test_every_surviving_mutant_is_a_listed_equivalent():
    found = survivors()
    unexplained = sorted(found - set(EQUIVALENT))
    stale = sorted(set(EQUIVALENT) - found)
    assert not unexplained and not stale, (
        f"unexplained survivors (add an arm): {unexplained}; "
        f"stale EQUIVALENT entries (remove): {stale}"
    )

