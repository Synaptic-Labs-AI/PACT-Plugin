"""No production code probes a CLAUDE.md from CLAUDE_PROJECT_DIR directly.

Location: pact-plugin/tests/test_project_dir_document_census.py

The session's project memory document comes from ONE place:
`project_scope.session_anchor` reads CLAUDE_PROJECT_DIR, `session_memory_root`
classifies it, and `claude_md_manager.resolve_managed_document` probes the
root. A function that takes the variable's value and probes a CLAUDE.md at it
on its own skips the classifier, so a linked worktree's own copy can be read
or written again. This census reads every production module's syntax tree
and flags a CLAUDE.md probe whose argument derives from a value that names
CLAUDE_PROJECT_DIR within the same function: a call to one of the document
probes, or a `/ "CLAUDE.md"` join.

The allow-list names each kept site and why it is not a second resolver.
"""
from __future__ import annotations

import ast
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent

PROBES = frozenset({
    "_find_existing_claude_md",
    "_first_document_under",
    "managed_document_paths",
    "resolve_managed_document",
    "resolve_project_claude_md_path",
})

ALLOWED = {
    ("scripts/archive_pin.py", "resolve_claude_md"): (
        "the scope guard probes the declared directory only to decide whether "
        "the session document it already resolved must be refused; it never "
        "uses what it finds there"
    ),
}


def _mentions(node, tainted) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value == "CLAUDE_PROJECT_DIR":
            return True
        if isinstance(sub, ast.Name) and sub.id in tainted:
            return True
    return False


def _tainted_names(function) -> set[str]:
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if _mentions(node.value, tainted):
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name) and name.id not in tainted:
                                tainted.add(name.id)
                                changed = True
    return tainted


def _probe_sites(tree) -> list[tuple[str, int]]:
    sites = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted = _tainted_names(function)
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else None
                )
                arguments = [*node.args, *(k.value for k in node.keywords)]
                if name in PROBES and any(_mentions(a, tainted) for a in arguments):
                    sites.append((function.name, node.lineno))
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, str)
                and node.right.value.endswith("CLAUDE.md")
                and _mentions(node.left, tainted)
            ):
                sites.append((function.name, node.lineno))
    return sites


def _production_sites() -> dict[tuple[str, str], list[int]]:
    found: dict[tuple[str, str], list[int]] = {}
    for path in sorted(PLUGIN.rglob("*.py")):
        relative = path.relative_to(PLUGIN)
        if relative.parts[0] == "tests":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function, line in _probe_sites(tree):
            found.setdefault((relative.as_posix(), function), []).append(line)
    return found


def test_no_function_probes_a_document_from_the_variable():
    found = _production_sites()

    unexpected = {site: lines for site, lines in found.items() if site not in ALLOWED}

    assert not unexpected, (
        "these functions probe a CLAUDE.md from CLAUDE_PROJECT_DIR instead of "
        f"session_document(): {unexpected}"
    )


def test_every_allowed_site_is_still_present():
    """An entry that no longer matches is a dead exemption a later site could
    hide behind."""
    found = _production_sites()

    assert set(ALLOWED) <= set(found), sorted(set(ALLOWED) - set(found))


def test_the_census_flags_the_shape_it_exists_to_catch():
    """The pre-fix resolver's shape: the variable read into a name, the name
    wrapped in a Path, the Path probed. Plus the direct join form."""
    tree = ast.parse(
        "def resolve(project_dir=None):\n"
        "    if project_dir is None:\n"
        "        project_dir = os.environ.get('CLAUDE_PROJECT_DIR')\n"
        "    base = Path(project_dir)\n"
        "    return _find_existing_claude_md(base)\n"
        "def join():\n"
        "    return Path(os.environ['CLAUDE_PROJECT_DIR']) / '.claude' / 'CLAUDE.md'\n"
        "def clean():\n"
        "    session = session_memory_root()\n"
        "    return resolve_managed_document(session.root)\n"
    )

    assert sorted(_probe_sites(tree)) == [("join", 7), ("resolve", 5)]
