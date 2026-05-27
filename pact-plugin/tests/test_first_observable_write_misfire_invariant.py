"""
Cross-file AST invariant for the FIRST-OBSERVABLE-WRITE class.

This invariant pins one architectural shape — once empirically observed and
mitigated — into executable form so a future regression that re-introduces it
fails loudly at CI time rather than during a live session.

The shape: a hook checks a multi-write-protocol invariant at the FIRST
observable write of the protocol, when the invariant cannot yet hold
(downstream writes have not happened). The hook either (a) fires
spuriously on every canonical-shape dispatch or (b) is structurally
unsatisfiable by construction. Either way the advisory becomes noise that
trains the editing LLM to dismiss legitimate signal.

Worked example (the one this invariant directly protects):
`teachback_addblocks_missing` was previously fired inside
`task_lifecycle_gate.py`'s TaskCreate branch, but `addBlocks=[<work_task_id>]`
cannot be set at TaskCreate(A) time because the work-task id does not yet
exist. The rule was re-timed to the wiring TaskUpdate boundary (where the
lead can satisfy both clauses in one update). This test asserts the rule
never returns to the TaskCreate branch.

Scope: this invariant currently denylists a single rule (the one above).
The hypothesized parent class COUNT-BASED-LIFECYCLE-INVARIANT-MISFIRE has
only two known instances at the time of writing; broadening this AST gate
to enforce the parent class would over-constrain hook design while the
class taxonomy is still under-specified. If a third instance crystallizes,
re-shape this test from a denylist lookup into a walk-and-assert that
flags any rule named `*_addblocks_missing` / `*_blockedby_missing` / etc.
sitting inside a TaskCreate branch.

The invariant is intentionally narrow: it does NOT enumerate hook files
generally. It scopes to `task_lifecycle_gate.py` because that's where the
FIRST-OBSERVABLE-WRITE instance lived. Generalization to other hooks (e.g.,
`wake_lifecycle_emitter.py`) is deliberately deferred to a future
architectural-class consolidation pass.
"""

import ast
from pathlib import Path


HOOK_FILE = (
    Path(__file__).parent.parent / "hooks" / "task_lifecycle_gate.py"
)


# Rules that enforce a multi-write-protocol invariant — they CANNOT live
# inside the TaskCreate branch because the protocol they enforce has not
# yet completed at TaskCreate time. Adding a rule to this tuple is a
# deliberate architectural-class assertion; do not append casually.
MULTI_WRITE_PROTOCOL_RULES: tuple[str, ...] = (
    "teachback_addblocks_missing",
)


def _find_taskcreate_branch(tree: ast.AST) -> ast.If | None:
    """Return the `if tool_name == "TaskCreate":` AST node inside
    evaluate_lifecycle, or None if not found. The lookup is structural —
    matches an If whose test compares the Name `tool_name` against the
    string constant `"TaskCreate"`.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        left = test.left
        if not (isinstance(left, ast.Name) and left.id == "tool_name"):
            continue
        if len(test.comparators) != 1:
            continue
        comparator = test.comparators[0]
        if not (isinstance(comparator, ast.Constant) and comparator.value == "TaskCreate"):
            continue
        if not (len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
            continue
        return node
    return None


def _collect_advisory_rule_names(branch: ast.AST) -> list[str]:
    """Walk an AST subtree, collect the rule-name string literal from each
    `advisories.append((<rule_name>, <message>))` call. Returns the list
    of literal rule names found.
    """
    rule_names: list[str] = []
    for node in ast.walk(branch):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "append":
            continue
        # advisories.append(...) — match the receiver name conservatively
        receiver = func.value
        if not (isinstance(receiver, ast.Name) and receiver.id == "advisories"):
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        # advisory tuples are 2-element (rule_name, message).
        if not isinstance(arg0, ast.Tuple) or not arg0.elts:
            continue
        first = arg0.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            rule_names.append(first.value)
    return rule_names


def test_no_multi_write_protocol_rule_in_taskcreate_branch():
    """AST gate: the TaskCreate branch of evaluate_lifecycle must not
    fire any rule that enforces a multi-write-protocol invariant. Such
    rules belong at a write-time TaskUpdate (or completion-time) boundary
    where the downstream writes have happened and the invariant can be
    satisfied or falsified.
    """
    source = HOOK_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(HOOK_FILE))
    branch = _find_taskcreate_branch(tree)
    assert branch is not None, (
        f"Could not locate `if tool_name == \"TaskCreate\":` branch in "
        f"{HOOK_FILE}. The AST invariant cannot evaluate without this "
        "structural landmark — has evaluate_lifecycle been restructured?"
    )
    rule_names = _collect_advisory_rule_names(branch)
    offending = [r for r in rule_names if r in MULTI_WRITE_PROTOCOL_RULES]
    assert offending == [], (
        f"Multi-write-protocol rule(s) found inside the TaskCreate branch "
        f"of {HOOK_FILE.name}: {offending}. These rules enforce invariants "
        f"that span multiple writes; firing them at TaskCreate is either "
        f"spurious (the downstream writes have not happened) or "
        f"structurally unsatisfiable (the work-task id has not yet been "
        f"assigned). Move the rule to a TaskUpdate (or completion-time) "
        f"boundary where the invariant can be honestly evaluated."
    )


def test_denylist_is_non_empty():
    """Documentation-in-code: the denylist MUST carry at least one rule
    name. An empty denylist would make the invariant test trivially
    vacuous (it would assert the absence of nothing). If a future cleanup
    proposes emptying this tuple, the architectural-class taxonomy
    should be re-evaluated first — an empty denylist with no replacement
    invariant is a signal that the class has stopped being load-bearing,
    which deserves explicit consideration rather than a silent drop.
    """
    assert MULTI_WRITE_PROTOCOL_RULES, (
        "MULTI_WRITE_PROTOCOL_RULES must carry at least one rule name. "
        "If the FIRST-OBSERVABLE-WRITE class is no longer load-bearing, "
        "delete this test file rather than leaving an empty denylist."
    )
