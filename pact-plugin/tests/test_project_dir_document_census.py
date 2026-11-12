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
import subprocess
from pathlib import Path

from fixtures.tracked_files import tracked_glob as _shared_tracked_glob

PLUGIN = Path(__file__).resolve().parent.parent

PROBES = frozenset({
    "_find_existing_claude_md",
    "_first_document_under",
    "managed_document_paths",
    "resolve_managed_document",
    "resolve_project_claude_md_path",
})

# Parameters that carry the declared project directory by convention. The
# intra-procedural taint below only sees a value ASSIGNED from the literal, so
# a probe that RECEIVES the directory as an argument was invisible to this
# census — the exact shape `test_no_function_probes_a_document_from_the_variable`
# is named after. These names seed the taint at the function boundary. The set
# is deliberately narrow: seeding every parameter flags the probe helpers
# themselves and the session-document resolvers (measured: eight sites in the
# shipped tree, none of them defects).
PROJECT_DIR_PARAMETERS = frozenset({
    "project_dir", "project_root", "claude_project_dir",
})

ALLOWED = {
    ("scripts/archive_pin.py", "resolve_claude_md"): (
        "the scope guard probes the declared directory only to decide whether "
        "the session document it already resolved must be refused; it never "
        "uses what it finds there"
    ),
    ("hooks/shared/project_scope.py", "_config_document_under_the_declared_project"): (
        "names where a relative CLAUDE_CONFIG_DIR's user-global file sits when "
        "read against the declaration, so that location is REFUSED; nothing "
        "reads or writes the path it returns"
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
    args = function.args
    tainted: set[str] = {
        arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if arg.arg in PROJECT_DIR_PARAMETERS
    }
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
    """Production probe sites, from the git index.

    An untracked scratch module must not be judged by this census; the
    session-init hook is the known member that keeps a wrong root loud.
    """
    found: dict[tuple[str, str], list[int]] = {}
    for path in _shared_tracked_glob(
            PLUGIN.parent, f"{PLUGIN.name}/**/*.py",
            f"{PLUGIN.name}/hooks/session_init.py"):
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


def test_untracked_module_cannot_join_the_production_census(
        tmp_path, monkeypatch):
    """The census reads the index; an untracked production module is invisible.

    Reverting ``_production_sites`` to ``PLUGIN.rglob("*.py")`` keeps the
    shipped tree green; this arm plants the probe shape the census exists to
    catch and reddens naming it. The seeded tracked module is the control.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    hooks = plugin / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "session_init.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "PLUGIN", plugin)

    assert list(_production_sites()) == [], (
        f"positive control: the seeded clean module was reported: "
        f"{_production_sites()}")
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "zzprobe.py").write_text(
        "def probe(project_dir):\n"
        "    return _find_existing_claude_md(project_dir)\n", encoding="utf-8")

    test_no_function_probes_a_document_from_the_variable()


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
        "def from_parameter(project_dir):\n"
        "    return _find_existing_claude_md(project_dir)\n"
        "def clean():\n"
        "    session = session_memory_root()\n"
        "    return resolve_managed_document(session.root)\n"
        "def unrelated(value):\n"
        "    return _find_existing_claude_md(value)\n"
    )

    # `from_parameter` is the shape the ARM IS NAMED AFTER: the declared
    # project directory arrives as an argument and is probed without ever
    # passing through the literal in this function. `unrelated` keeps the
    # parameter seed bounded: a parameter NOT named as the project directory
    # must stay invisible.
    assert sorted(_probe_sites(tree)) == [
        ("from_parameter", 9), ("join", 7), ("resolve", 5)]
