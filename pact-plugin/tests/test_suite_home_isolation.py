"""
Location: pact-plugin/tests/test_suite_home_isolation.py
Summary: Guards the suite-wide sandbox HOME that tests/conftest.py sets at import:
a process a test starts must resolve its home and its Claude config root outside
the operator's real home directory.
Used by: pytest (the whole suite depends on the conftest it checks).
"""

import ast
import os
import pwd
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_glob as _shared_tracked_glob

_PLUGIN = Path(__file__).resolve().parent.parent


def _test_source_files():
    """Test sources from the git index, one glob per population.

    An untracked scratch test file must not be judged by this scan; the two
    seeded modes are the known members that keep an empty root loud.
    """
    name = _PLUGIN.name
    return (
        _shared_tracked_glob(
            _PLUGIN.parent, f"{name}/tests/**/*.py",
            f"{name}/tests/test_suite_home_isolation.py")
        + _shared_tracked_glob(
            _PLUGIN.parent, f"{name}/skills/*/test_*.py",
            f"{name}/skills/pact-handoff-harvest/test_skill_loading_harvest.py")
    )
_HOOKS = _PLUGIN / "hooks"

# Read at collection, before any fixture runs: what import-time code in a test
# module, or a child started from it, would see.
_CONFIG_DIR_AT_COLLECTION = os.environ.get("CLAUDE_CONFIG_DIR")
_HOME_AT_COLLECTION = os.environ.get("HOME", "")

_CHILD = (
    "import os, pathlib\n"
    "from shared.paths import get_claude_config_dir\n"
    "config_dir = os.environ.get('CLAUDE_CONFIG_DIR')\n"
    "print(config_dir)\n"
    "print(pathlib.Path.home())\n"
    "print(get_claude_config_dir())\n"
)


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def _outside_real_home(path: str) -> bool:
    resolved = Path(path).resolve()
    real = _real_home()
    return resolved != real and real not in resolved.parents


def test_a_child_process_resolves_its_home_and_config_root_outside_the_real_home():
    env = dict(os.environ, PYTHONPATH=str(_HOOKS))
    out = subprocess.run(
        [sys.executable, "-c", _CHILD], env=env,
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.splitlines()
    assert out[0] == "None", out
    assert _outside_real_home(out[1]), out
    assert _outside_real_home(out[2]), out


def test_the_session_home_is_not_the_real_home():
    assert _outside_real_home(os.environ["HOME"])
    assert "CLAUDE_CONFIG_DIR" not in os.environ


def test_collection_already_runs_in_the_sandbox():
    assert _CONFIG_DIR_AT_COLLECTION is None
    assert _outside_real_home(_HOME_AT_COLLECTION)


def test_the_embedding_cache_is_not_moved_into_the_sandbox_home():
    # The sandbox HOME must not hand the embedding tests a cold cache.
    cache = Path(os.environ["HF_HOME"]).resolve()
    sandbox = Path(os.environ["HOME"]).resolve()
    assert cache != sandbox and sandbox not in cache.parents, (cache, sandbox)


HAS, LACKS, UNKNOWN = "has", "lacks", "unknown"
_ENVIRON = frozenset({"os.environ", "dict(os.environ)", "os.environ.copy()"})


def _home_verdict(node: ast.expr, built: dict, seen: frozenset = frozenset()) -> str:
    """Whether an `env=` value carries HOME, as far as this scope shows.

    A child env without HOME finds its home through the password database: the
    real one. HAS: os.environ or a copy of it (the conftest's sandbox HOME
    rides along), `env=None` (inherit), or an explicit HOME key. LACKS: a dict
    built here with no HOME key whose spreads, if any, carry no HOME either; a
    spread of anything not shown to carry HOME counts as lacking it. A name is
    resolved through every value this scope assigns to it.

    UNKNOWN, and therefore passed, because judging it needs another function or
    a run: a helper call (`_env(root)`, `world.env()`), a name the scope does
    not bind (a parameter or a global), an attribute or subscript (`w.env`,
    `calls[0][1]["env"]`), and a comprehension over `os.environ.items()`, whose
    filter could drop HOME only by evaluating its condition.
    """
    while isinstance(node, ast.Call) and getattr(node.func, "id", "") == "carry_clock_shift" \
            and node.args:
        node = node.args[0]
    if isinstance(node, ast.Constant) and node.value is None:
        return HAS
    if ast.unparse(node) in _ENVIRON:
        return HAS
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in built:
            return UNKNOWN
        verdicts = {_home_verdict(v, built, seen | {node.id}) for v in built[node.id]}
        return verdicts.pop() if len(verdicts) == 1 else UNKNOWN
    if isinstance(node, ast.Dict):
        keys = [k.value if isinstance(k, ast.Constant) else k for k in node.keys]
        if "HOME" in keys:
            return HAS
        if any(k is not None and not isinstance(k, str) for k in keys):
            return UNKNOWN
        spreads = [v for k, v in zip(node.keys, node.values) if k is None]
        return HAS if any(_home_verdict(s, built, seen) == HAS for s in spreads) else LACKS
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
        if any(k.arg == "HOME" for k in node.keywords):
            return HAS
        parts = [*node.args[:1], *(k.value for k in node.keywords if k.arg is None)]
        verdicts = [_home_verdict(part, built, seen) for part in parts]
        if HAS in verdicts:
            return HAS
        return UNKNOWN if UNKNOWN in verdicts and node.args else LACKS
    return UNKNOWN


def _home_removed(scope: ast.AST) -> set:
    """Names this scope strips HOME from: `env.pop("HOME", ...)` or `del env["HOME"]`."""
    removed = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "pop" and isinstance(node.func.value, ast.Name) \
                and node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "HOME":
            removed.add(node.func.value.id)
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                        and isinstance(target.slice, ast.Constant) \
                        and target.slice.value == "HOME":
                    removed.add(target.value.id)
    return removed


def _seeded_rig(tmp_path, monkeypatch, extra=None):
    """A git-indexed plugin tree with 102 clean test sources, plus `extra`
    ({relative path: source}) tracked beside them; the scan reads it."""
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    tests_dir = plugin / "tests"
    tests_dir.mkdir(parents=True)
    # The production arm carries its own `len(files) > 100` denominator guard,
    # so the fixture must clear it to reach the assertion under test.
    (tests_dir / "test_suite_home_isolation.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")
    for index in range(100):
        (tests_dir / f"test_known_{index}.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")
    known = plugin / "skills/pact-handoff-harvest/test_skill_loading_harvest.py"
    known.parent.mkdir(parents=True)
    known.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    for relative, source in (extra or {}).items():
        (plugin / relative).write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "_PLUGIN", plugin)
    return plugin


@pytest.mark.parametrize("relative", [
    "tests/zzprobe.py", "skills/zzprobe/test_zzprobe.py",
], ids=["tests", "skills"])
def test_untracked_test_file_cannot_join_the_home_scan(
        tmp_path, monkeypatch, relative):
    """Both populations read the index; an untracked test file is invisible.

    Reverting ``_test_source_files`` to the two filesystem globs keeps the
    shipped tree green; this arm drives the HOME scan and reddens naming the
    untracked file. The seeded tracked files are the positive control.
    """
    plugin = _seeded_rig(tmp_path, monkeypatch)

    assert len(_test_source_files()) == 102, (
        f"positive control: expected the 102 seeded sources, got "
        f"{len(_test_source_files())}")
    probe = plugin / relative
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import subprocess\n\n\n"
        "def test_child():\n"
        "    env = {'X': '1'}\n"
        "    subprocess.run(['true'], env=env)\n", encoding="utf-8")

    test_no_child_environment_built_in_a_test_leaves_out_home()


def test_no_child_environment_built_in_a_test_leaves_out_home():
    """Every `env=` a test passes to a subprocess runner carries HOME, or strips
    none. What is judged, and which shapes are passed unjudged and why, is in
    _home_verdict's docstring."""
    runners = {"run", "Popen", "check_output", "check_call", "call"}
    files = _test_source_files()
    misses = []
    for path in files:
        if "fixtures" in path.parts:
            continue
        for scope in ast.walk(ast.parse(path.read_text(), str(path))):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                continue
            built: dict[str, list[ast.expr]] = {}
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name):
                    built.setdefault(node.targets[0].id, []).append(node.value)
            removed = _home_removed(scope)
            for node in ast.walk(scope):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in runners):
                    continue
                for kw in node.keywords:
                    if kw.arg != "env":
                        continue
                    value = target = kw.value
                    while isinstance(target, ast.Call) and target.args \
                            and getattr(target.func, "id", "") == "carry_clock_shift":
                        target = target.args[0]
                    stripped = isinstance(target, ast.Name) and target.id in removed
                    if stripped or _home_verdict(value, built) == LACKS:
                        misses.append(f"{path.relative_to(_PLUGIN)}:{node.lineno}")
    assert len(files) > 100, files
    assert not sorted(set(misses)), sorted(set(misses))


def _verdict(source: str, scope_source: str = "") -> str:
    """The verdict for one env expression, inside a function whose body is
    `scope_source` (assignments the scanner resolves names through)."""
    module = ast.parse(f"def probe(param):\n{textwrap.indent(scope_source or 'pass', '    ')}\n"
                       f"    subprocess.run([], env={source})\n")
    scope = module.body[0]
    built: dict[str, list[ast.expr]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            built.setdefault(node.targets[0].id, []).append(node.value)
    call = next(n for n in ast.walk(scope) if isinstance(n, ast.Call)
                and getattr(n.func, "attr", "") == "run")
    value = next(k.value for k in call.keywords if k.arg == "env")
    target = value
    while isinstance(target, ast.Call) and target.args \
            and getattr(target.func, "id", "") == "carry_clock_shift":
        target = target.args[0]
    if isinstance(target, ast.Name) and target.id in _home_removed(scope):
        return LACKS
    return _home_verdict(value, built)


@pytest.mark.parametrize("source, scope_source, verdict", [
    # The os.environ allowance: a spread or copy of it carries the sandbox HOME.
    ("{**os.environ}", "", HAS),
    ("{**os.environ, 'X': '1'}", "", HAS),
    ("{**dict(os.environ), 'X': '1'}", "", HAS),
    ("{**os.environ.copy()}", "", HAS),
    ("{**base, 'X': '1'}", "base = os.environ.copy()", HAS),
    ("dict(os.environ, X='1')", "", HAS),
    ("os.environ.copy()", "", HAS),
    ("None", "", HAS),
    # An explicit HOME key wins over any spread.
    ("{**param, 'HOME': '/sandbox'}", "", HAS),
    ("dict(param, HOME='/sandbox')", "", HAS),
    # A spread of anything not shown to carry HOME, with no HOME key, lacks it.
    ("{**param}", "", LACKS),
    ("{**param, 'X': '1'}", "", LACKS),
    ("{**helper(), 'X': '1'}", "", LACKS),
    ("{**base}", "base = {'PATH': '/bin'}", LACKS),
    ("{'X': '1'}", "", LACKS),
    ("dict(X='1')", "", LACKS),
    ("dict(base, X='1')", "base = {'PATH': '/bin'}", LACKS),
    # Names resolve through this scope, including through carry_clock_shift.
    ("carry_clock_shift(env)", "env = {'PATH': '/bin'}", LACKS),
    ("carry_clock_shift(env)", "env = {'HOME': '/h'}", HAS),
    ("env", "env = carry_clock_shift(base)\nbase = {'PATH': '/bin'}", LACKS),
    # HOME stripped from a name after it is built.
    ("env", "env = dict(os.environ)\nenv.pop('HOME', None)", LACKS),
    ("env", "env = dict(os.environ)\ndel env['HOME']", LACKS),
    ("carry_clock_shift(env)", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    # Not judgeable from this scope: passed.
    ("helper()", "", UNKNOWN),
    ("param", "", UNKNOWN),
    ("dict(param, X='1')", "", UNKNOWN),
    ("w.env", "", UNKNOWN),
    ("{k: v for k, v in os.environ.items() if k != 'X'}", "", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv = helper()", UNKNOWN),
    ("env", "env = env", UNKNOWN),
])
def test_the_scanner_judges_each_env_shape(source, scope_source, verdict):
    assert _verdict(source, scope_source) == verdict


@pytest.mark.parametrize("probe, flagged", [
    ("def test_child(base):\n    subprocess.run(['true'], env={**base, 'X': '1'})\n", True),
    ("def test_child():\n    env = {**os.environ, 'X': '1'}\n"
     "    subprocess.run(['true'], env=env)\n", False),
], ids=["spread-without-home", "environ-spread"])
def test_the_scan_flags_a_spread_env_without_home(tmp_path, monkeypatch, probe, flagged):
    """Through the whole scan: a tracked test whose child env spreads something
    that carries no HOME is named; one spreading os.environ is not."""
    _seeded_rig(tmp_path, monkeypatch, {
        "tests/test_zzspread.py": "import os\nimport subprocess\n\n\n" + probe})
    if flagged:
        with pytest.raises(AssertionError, match=r"tests/test_zzspread\.py:6"):
            test_no_child_environment_built_in_a_test_leaves_out_home()
    else:
        test_no_child_environment_built_in_a_test_leaves_out_home()
