"""Every hooks module that copies a patched shared name by value is loaded by conftest.

`from shared.m import f` keeps whatever object `f` was at the module's first
import. If a test patches `shared.m.f` and the module is first imported inside
that patch, the stand-in stays bound for the rest of the process. tests/conftest.py
imports such modules before any test runs, so the names they copy are the real
objects. These arms derive that population from the tree: they fail when a module
is missing from conftest's guard, and when a guard line or exemption no longer
names such a module.
"""

import ast
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_files as _tracked_files

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
CONFTEST = PLUGIN / "tests" / "conftest.py"


def _tracked_plugin_python_files(plugin: Path) -> list[Path]:
    repo_root = plugin.parent
    relative = plugin.relative_to(repo_root).as_posix()
    return sorted(path for path in _tracked_files(repo_root, relative)
                  if path.suffix == ".py")


def _census_test_files(plugin: Path) -> list[Path]:
    files = _tracked_plugin_python_files(plugin)
    tests = plugin / "tests"
    skill_files = [p for p in files if "skills" in p.relative_to(plugin).parts]
    return sorted(
        [p for p in files if tests in p.parents]
        + [p for p in skill_files if p.name.startswith("test_")]
        + [p for p in skill_files if p.name == "conftest.py"]
    )


def _tracked_hook_python_files(hooks: Path) -> list[Path]:
    repo_root = hooks.parent.parent
    relative = hooks.relative_to(repo_root).as_posix()
    return sorted(path for path in _tracked_files(repo_root, relative)
                  if path.suffix == ".py")


def _shared_module_stems(hooks: Path) -> set[str]:
    return {
        path.stem
        for path in _tracked_hook_python_files(hooks)
        if path.parent == hooks / "shared" and path.stem != "__init__"
    }

# {module: reason}, only for a module that no test imports in-process.
EXEMPT: dict = {}

_TRY_NODES = tuple({ast.Try, getattr(ast, "TryStar", ast.Try)})


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _module_level(statements):
    """Module-level statements, including those inside try, if and with blocks."""
    stack = list(statements)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _TRY_NODES):
            stack.extend(node.body + node.orelse + node.finalbody)
            for handler in node.handlers:
                stack.extend(handler.body)
        elif isinstance(node, ast.If):
            stack.extend(node.body + node.orelse)
        elif isinstance(node, ast.With):
            stack.extend(node.body)


def _module_name(path, hooks_root):
    parts = list(path.relative_to(hooks_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _shared_owner(owner, shared_modules, hooks_root):
    """The shared module a dotted patch owner names, or None.

    `shared.m` names it directly. `hook.m`, where `hook` is a hooks module,
    names the same module object through that hook's attribute.
    """
    parts = owner.split(".")
    if len(parts) != 2 or parts[1] not in shared_modules:
        return None
    if parts[0] == "shared" or (hooks_root / f"{parts[0]}.py").is_file():
        return parts[1]
    return None


def patched_names(test_files, shared_modules, hooks_root):
    """(module, attr) pairs that a test patches on hooks/shared/<module>."""
    pairs = set()
    for path in test_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        alias = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    if name.asname:
                        alias[name.asname] = name.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for name in node.names:
                    alias[name.asname or name.name] = f"{node.module}.{name.name}"

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = _dotted(node.func) or ""
            args = node.args
            owner = attr = None
            if (func.split(".")[-1] in ("setattr", "patch") and args
                    and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)):
                owner, _, attr = args[0].value.rpartition(".")
            elif ((func.split(".")[-1] == "setattr" or func.endswith("patch.object"))
                    and len(args) >= 2
                    and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str)):
                dotted = _dotted(args[0])
                if dotted:
                    head, _, rest = dotted.partition(".")
                    owner = alias.get(head, head) + (f".{rest}" if rest else "")
                    attr = args[1].value
            module = _shared_owner(owner, shared_modules, hooks_root) if owner else None
            if module:
                pairs.add((module, attr))
    return pairs


def by_value_importers(hooks_root, patched):
    """{module: [(source module, name), ...]} for every hooks module whose
    module-level body copies a patched shared name by value."""
    found = {}
    for path in _tracked_hook_python_files(hooks_root):
        if "__pycache__" in path.parts:
            continue
        in_shared = path.parent == hooks_root / "shared"
        copies = set()
        for node in _module_level(ast.parse(path.read_text(encoding="utf-8")).body):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.level == 1 and in_shared:
                source = node.module
            elif node.level == 0 and node.module.startswith("shared."):
                source = node.module[len("shared."):]
            else:
                continue
            copies |= {(source, n.name) for n in node.names if (source, n.name) in patched}
        if copies:
            found[_module_name(path, hooks_root)] = sorted(copies)
    return found


def guarded_modules(conftest, hooks_root):
    """(lines, loaded). `lines` are the hooks modules conftest imports at module
    level. `loaded` adds the shared package and the shared modules its
    __init__ imports, which importing any hooks module loads first."""
    lines = set()
    for node in _module_level(ast.parse(conftest.read_text(encoding="utf-8")).body):
        if isinstance(node, ast.Import):
            candidates = [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            candidates = [node.module]
        else:
            continue
        for name in candidates:
            path = hooks_root.joinpath(*name.split("."))
            if path.with_suffix(".py").is_file() or (path / "__init__.py").is_file():
                lines.add(name)
    loaded = set(lines)
    init = hooks_root / "shared" / "__init__.py"
    if lines and init.is_file():
        loaded.add("shared")
        for node in _module_level(ast.parse(init.read_text(encoding="utf-8")).body):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                loaded.add(f"shared.{node.module.split('.')[0]}")
    return lines, loaded


def _census_values():
    test_files = _census_test_files(PLUGIN)
    shared_modules = _shared_module_stems(HOOKS)
    patched = patched_names(test_files, shared_modules, HOOKS)
    return patched, by_value_importers(HOOKS, patched), guarded_modules(CONFTEST, HOOKS)


@pytest.fixture(scope="module")
def census():
    return _census_values()


def _git_init(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "pact-plugin"],
        check=True, capture_output=True)


def test_census_test_population_ignores_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    tests = plugin / "tests"
    tests.mkdir(parents=True)
    known = tests / "test_known.py"
    known.write_text(
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(\"shared.pact_context.resolve_agent_name\", None)\n",
        encoding="utf-8")
    (tests / "conftest.py").write_text("", encoding="utf-8")
    hooks = plugin / "hooks"
    (hooks / "shared").mkdir(parents=True)
    (hooks / "shared" / "pact_context.py").write_text(
        "resolve_agent_name = None\n", encoding="utf-8")
    _git_init(repo)

    stems = _shared_module_stems(hooks)
    files = _census_test_files(plugin)
    assert known in files
    assert ("pact_context", "resolve_agent_name") in patched_names(files, stems, hooks)

    stray = tests / "test_zzprobe.py"
    stray.write_text(
        "def test_y(monkeypatch):\n"
        "    monkeypatch.setattr(\"shared.pact_context.is_lead\", None)\n",
        encoding="utf-8")
    try:
        files = _census_test_files(plugin)
        assert stray not in files
        assert ("pact_context", "is_lead") not in patched_names(files, stems, hooks)
    finally:
        stray.unlink()


def test_census_skill_populations_ignore_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    known_dir = plugin / "skills" / "known"
    known_dir.mkdir(parents=True)
    (known_dir / "test_known.py").write_text(
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(\"shared.pact_context.is_lead\", None)\n",
        encoding="utf-8")
    (known_dir / "conftest.py").write_text(
        "def test_y(monkeypatch):\n"
        "    monkeypatch.setattr(\"shared.pact_context.get_team_name\", None)\n",
        encoding="utf-8")
    hooks = plugin / "hooks"
    (hooks / "shared").mkdir(parents=True)
    (hooks / "shared" / "pact_context.py").write_text(
        "is_lead = None\n", encoding="utf-8")
    _git_init(repo)

    stems = _shared_module_stems(hooks)
    files = _census_test_files(plugin)
    pairs = patched_names(files, stems, hooks)
    assert known_dir / "test_known.py" in files
    assert known_dir / "conftest.py" in files
    assert ("pact_context", "is_lead") in pairs
    assert ("pact_context", "get_team_name") in pairs

    stray_dir = plugin / "skills" / "zzprobe"
    stray_dir.mkdir(parents=True)
    stray_test = stray_dir / "test_zzprobe.py"
    stray_test.write_text(
        "def test_z(monkeypatch):\n"
        "    monkeypatch.setattr(\"shared.pact_context.resolve_actor\", None)\n",
        encoding="utf-8")
    stray_conftest = stray_dir / "conftest.py"
    stray_conftest.write_text(
        "def test_w(monkeypatch):\n"
        "    monkeypatch.setattr(\"shared.pact_context.resolve_agent_name\", None)\n",
        encoding="utf-8")
    try:
        files = _census_test_files(plugin)
        assert stray_test not in files
        assert stray_conftest not in files
        pairs = patched_names(files, stems, hooks)
        assert ("pact_context", "resolve_actor") not in pairs
        assert ("pact_context", "resolve_agent_name") not in pairs
    finally:
        stray_test.unlink()
        stray_conftest.unlink()


def test_shared_module_stems_ignore_untracked_modules(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    hooks = plugin / "hooks"
    (hooks / "shared").mkdir(parents=True)
    known = hooks / "shared" / "known.py"
    known.write_text("X = 1\n", encoding="utf-8")
    _git_init(repo)

    assert _shared_module_stems(hooks) == {"known"}

    stray = hooks / "shared" / "zzprobeattr.py"
    stray.write_text("thing = 1\n", encoding="utf-8")
    try:
        assert _shared_module_stems(hooks) == {"known"}
    finally:
        stray.unlink()


def test_by_value_importers_ignore_untracked_hooks(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    hooks = plugin / "hooks"
    hooks.mkdir(parents=True)
    known = hooks / "known.py"
    known.write_text(
        "from shared.pact_context import resolve_agent_name\n", encoding="utf-8")
    _git_init(repo)
    patched = {("pact_context", "resolve_agent_name")}

    assert by_value_importers(hooks, patched) == {
        "known": [("pact_context", "resolve_agent_name")]}

    stray = hooks / "zzprobe.py"
    stray.write_text(
        "from shared.pact_context import resolve_agent_name\n", encoding="utf-8")
    try:
        assert by_value_importers(hooks, patched) == {
            "known": [("pact_context", "resolve_agent_name")]}
    finally:
        stray.unlink()


def test_the_census_sees_a_known_patch_and_a_known_importer(census):
    patched, importers, (lines, loaded) = census
    assert ("pact_context", "get_team_name") in patched
    assert ("pact_context", "is_lead") in patched  # patched through a hook's attribute
    assert "shared.background_work" in importers
    assert "shared.background_work" in lines
    assert "shared.pact_context" in loaded  # loaded by the shared package's __init__


def test_every_by_value_importer_of_a_patched_name_is_loaded_by_conftest(census):
    _patched, importers, (_lines, loaded) = census
    missing = {m: importers[m] for m in sorted(set(importers) - loaded - set(EXEMPT))}
    assert not missing, (
        "these hooks modules copy a name that some test patches on its source "
        "module, and tests/conftest.py does not import them, so a test that "
        "patches the source before their first import leaves its stand-in bound: "
        f"{missing}. Import the module in conftest's guard, make the import "
        "late-binding, or list the module in EXEMPT with the reason no test "
        "imports it in-process."
    )


def test_no_guard_line_or_exemption_is_stale(census):
    _patched, importers, (lines, _loaded) = census
    stale_guard = sorted(lines - set(importers))
    stale_exempt = sorted(set(EXEMPT) - set(importers))
    assert not stale_guard and not stale_exempt, (
        f"conftest guards {stale_guard} and EXEMPT lists {stale_exempt}, but "
        "none of these copies a patched shared name any more; remove the entry."
    )


def test_an_import_inside_a_module_level_try_or_if_is_found(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "__init__.py").write_text("")
    (shared / "source.py").write_text("def f():\n    pass\n")
    (shared / "copier.py").write_text(
        "try:\n    from .source import f\nexcept ImportError:\n    f = None\n"
    )
    (tmp_path / "hook.py").write_text("if True:\n    from shared.source import f\n")
    (tmp_path / "late.py").write_text(
        "from shared import source\n\n\ndef f():\n    return source.f()\n"
    )

    found = by_value_importers(tmp_path, {("source", "f")})

    assert found == {"shared.copier": [("source", "f")], "hook": [("source", "f")]}
