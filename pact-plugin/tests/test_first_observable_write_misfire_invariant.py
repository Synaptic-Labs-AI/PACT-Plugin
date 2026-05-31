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
FIRST-OBSERVABLE-WRITE instance lived. Generalization to other hooks is
deliberately deferred to a future architectural-class consolidation pass.
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


def _find_taskcreate_branches(tree: ast.AST) -> list[ast.If]:
    """Return ALL `if tool_name == "TaskCreate":` AST nodes in the file,
    not just the first one matched. The single-branch assumption from
    the original implementation was a refactor-fragility hazard: if a
    future refactor introduces a sibling dispatcher function with its
    own TaskCreate branch and a denylisted rule moves into that second
    branch, returning only the first branch would silently pass the
    invariant.

    The lookup is structural — matches an If whose test compares the
    Name `tool_name` against the string constant `"TaskCreate"`.
    """
    branches: list[ast.If] = []
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
        branches.append(node)
    return branches


def _find_advisory_wrap_helpers(tree: ast.AST) -> set[str]:
    """Identify functions in the module whose body wraps
    `advisories.append((<param_name>, ...))` — i.e., the rule name comes
    from one of the function's own parameters. These are
    "wrap helpers": calling them with a string literal as the first
    argument is semantically equivalent to writing the literal directly
    into an `advisories.append` tuple at the call site.

    Returns the set of helper-function names. Phantom-green protection:
    without this resolution step, a refactor introducing
    `def _add(rule, msg): advisories.append((rule, msg))` and calling
    `_add("teachback_addblocks_missing", ...)` from inside the
    TaskCreate branch would silently pass the invariant — `ast.walk`
    finds no direct `advisories.append` call inside the branch.

    Only catches the one-hop direct-wrap pattern. Indirect chains
    (helper-calls-helper-that-appends) are NOT resolved — this is
    deliberately bounded to keep the AST analysis predictable.
    """
    helpers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        param_names = {a.arg for a in node.args.args}
        for body_node in ast.walk(node):
            if not isinstance(body_node, ast.Call):
                continue
            func = body_node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "append"):
                continue
            receiver = func.value
            if not (isinstance(receiver, ast.Name) and receiver.id == "advisories"):
                continue
            if not body_node.args:
                continue
            arg0 = body_node.args[0]
            if not (isinstance(arg0, ast.Tuple) and arg0.elts):
                continue
            first = arg0.elts[0]
            if isinstance(first, ast.Name) and first.id in param_names:
                helpers.add(node.name)
                break
    return helpers


def _collect_advisory_rule_names(branch: ast.AST, wrap_helpers: set[str]) -> list[str]:
    """Walk an AST subtree, collect rule-name string literals from BOTH:
    (1) direct `advisories.append((<rule_name>, <message>))` calls, and
    (2) calls to a known wrap-helper (resolves the literal rule-name
        passed at the call site).

    `wrap_helpers` is the set of function names returned by
    `_find_advisory_wrap_helpers`. Phantom-green protection: pass the
    wrap-helper set so helper-wrapped calls inside the branch are
    detected.
    """
    rule_names: list[str] = []
    for node in ast.walk(branch):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Pattern (1): direct advisories.append((rule, msg)) call.
        if isinstance(func, ast.Attribute) and func.attr == "append":
            receiver = func.value
            if isinstance(receiver, ast.Name) and receiver.id == "advisories":
                if node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Tuple) and arg0.elts:
                        first = arg0.elts[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            rule_names.append(first.value)
            continue
        # Pattern (2): wrap-helper call — resolve the literal first arg.
        helper_name: str | None = None
        if isinstance(func, ast.Name):
            helper_name = func.id
        elif isinstance(func, ast.Attribute):
            helper_name = func.attr
        if helper_name is None or helper_name not in wrap_helpers:
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            rule_names.append(arg0.value)
    return rule_names


def test_no_multi_write_protocol_rule_in_taskcreate_branch():
    """AST gate: NO `if tool_name == "TaskCreate":` branch in the file
    may fire a rule that enforces a multi-write-protocol invariant.
    Such rules belong at a write-time TaskUpdate (or completion-time)
    boundary where the downstream writes have happened and the
    invariant can be satisfied or falsified.

    Robust against two refactor-fragility hazards:
    (1) multiple TaskCreate branches in the file — all are enumerated;
    (2) helper-wrapped `advisories.append` calls — rule names passed
        through a one-hop wrap helper are still resolved at call sites.
    """
    source = HOOK_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(HOOK_FILE))
    branches = _find_taskcreate_branches(tree)
    assert branches, (
        f"Could not locate any `if tool_name == \"TaskCreate\":` branch "
        f"in {HOOK_FILE}. The AST invariant cannot evaluate without this "
        "structural landmark — has evaluate_lifecycle been restructured?"
    )
    wrap_helpers = _find_advisory_wrap_helpers(tree)
    offending: list[str] = []
    for branch in branches:
        for rule in _collect_advisory_rule_names(branch, wrap_helpers):
            if rule in MULTI_WRITE_PROTOCOL_RULES and rule not in offending:
                offending.append(rule)
    assert offending == [], (
        f"Multi-write-protocol rule(s) found inside a TaskCreate branch "
        f"of {HOOK_FILE.name}: {offending}. These rules enforce invariants "
        f"that span multiple writes; firing them at TaskCreate is either "
        f"spurious (the downstream writes have not happened) or "
        f"structurally unsatisfiable (the work-task id has not yet been "
        f"assigned). Move the rule to a TaskUpdate (or completion-time) "
        f"boundary where the invariant can be honestly evaluated."
    )


def test_helper_wrapped_appends_are_resolved_through_one_hop():
    """Phantom-green protection: synthetic source where the TaskCreate
    branch invokes a wrap helper with the denylisted rule name as a
    string-literal argument. The wrap helper's body forwards the
    parameter to advisories.append. Without one-hop resolution, the
    direct-append walker would find no advisories.append call inside
    the branch and the invariant would silently pass. With resolution,
    the offending rule is detected at the call site.
    """
    src = (
        "def _add_advisory(rule, msg):\n"
        "    advisories.append((rule, msg))\n"
        "\n"
        "def evaluate_lifecycle(input_data):\n"
        "    advisories = []\n"
        "    tool_name = input_data.get('tool_name')\n"
        "    if tool_name == 'TaskCreate':\n"
        "        _add_advisory('teachback_addblocks_missing', 'fake')\n"
        "    return advisories\n"
    )
    tree = ast.parse(src)
    wrap_helpers = _find_advisory_wrap_helpers(tree)
    assert "_add_advisory" in wrap_helpers, (
        f"Wrap helper detector failed to identify _add_advisory; "
        f"detected helpers: {wrap_helpers}"
    )
    branches = _find_taskcreate_branches(tree)
    assert len(branches) == 1
    rule_names = _collect_advisory_rule_names(branches[0], wrap_helpers)
    assert "teachback_addblocks_missing" in rule_names, (
        f"One-hop wrap-helper resolution failed to surface the rule "
        f"passed via the helper. Detected rules: {rule_names}"
    )


def test_multiple_taskcreate_branches_are_all_inspected():
    """Phantom-green protection: synthetic source with TWO sibling
    `if tool_name == "TaskCreate":` branches in different functions.
    A denylisted rule moved into the second branch must be caught.
    Without multi-branch enumeration, the original single-branch walker
    would return only the first branch and the offending second-branch
    rule would silently pass.
    """
    src = (
        "def evaluate_lifecycle(input_data):\n"
        "    advisories = []\n"
        "    tool_name = input_data.get('tool_name')\n"
        "    if tool_name == 'TaskCreate':\n"
        "        advisories.append(('work_addblockedby_missing', 'ok'))\n"
        "    return advisories\n"
        "\n"
        "def evaluate_lifecycle_sibling_dispatch(input_data):\n"
        "    advisories = []\n"
        "    tool_name = input_data.get('tool_name')\n"
        "    if tool_name == 'TaskCreate':\n"
        "        advisories.append(('teachback_addblocks_missing', 'fake'))\n"
        "    return advisories\n"
    )
    tree = ast.parse(src)
    branches = _find_taskcreate_branches(tree)
    assert len(branches) == 2, (
        f"Multi-branch enumerator returned {len(branches)} branches; "
        f"expected 2 (one per evaluate_lifecycle*)."
    )
    wrap_helpers = _find_advisory_wrap_helpers(tree)
    all_rules: list[str] = []
    for branch in branches:
        all_rules.extend(_collect_advisory_rule_names(branch, wrap_helpers))
    assert "teachback_addblocks_missing" in all_rules, (
        f"Multi-branch walker failed to surface the rule from the "
        f"second branch. Detected rules across all branches: {all_rules}"
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
