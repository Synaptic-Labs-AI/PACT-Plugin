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
_RUNNER_ATTRS = frozenset({
    "run", "Popen", "check_output", "check_call", "call",
    "create_subprocess_exec", "create_subprocess_shell",
})
_SUBPROCESS_RUNNERS = frozenset({"run", "Popen", "check_output", "check_call", "call"})

# The shapes the scan judges. Every other shape is unjudged and passed; the
# scan's docstring lists examples. Each entry is pinned to a verdict row.
JUDGED_SHAPES = (
    "environ",              # os.environ, dict(os.environ), os.environ.copy()
    "none",                 # env=None: the child inherits
    "dict-literal",         # {...}: an explicit HOME key, else its spreads decide
    "spread",               # {**x}: x judged recursively
    "dict-call",            # dict(x, K=...): HOME= keyword, else x and **kw decide
    "union",                # a | b: has if either side has HOME
    "name",                 # a name, through its last binding in this function
    "annotated-binding",    # env: T = value
    "post-build-write",     # env["HOME"] = / update / setdefault / |= {"HOME": ...}
    "strip",                # env.pop("HOME") / del env["HOME"]
    "carry-clock-shift",    # carry_clock_shift(x): x judged
    "bare-runner",          # run/Popen/... imported from subprocess, aliases included
    "asyncio-runner",       # asyncio.create_subprocess_exec / _shell
)


def _local_nodes(scope):
    """Every node of `scope` except those inside a nested function or lambda,
    which are scopes of their own."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _is_home_key(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == "HOME"


def _name_events(nodes) -> dict:
    """name -> [(lineno, col, kind, value)], kind one of bind, strip, add."""
    events: dict = {}

    def add(name, node, kind, value=None):
        events.setdefault(name, []).append((node.lineno, node.col_offset, kind, value))

    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    add(target.id, node, "bind", node.value)
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                        and _is_home_key(target.slice):
                    add(target.value.id, node, "add")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.value is not None:
            add(node.target.id, node, "bind", node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                and isinstance(node.op, ast.BitOr) and isinstance(node.value, ast.Dict) \
                and any(_is_home_key(k) for k in node.value.keys):
            add(node.target.id, node, "add")
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                        and _is_home_key(target.slice):
                    add(target.value.id, node, "strip")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name):
            name, method = node.func.value.id, node.func.attr
            if method == "pop" and node.args and _is_home_key(node.args[0]):
                add(name, node, "strip")
            elif method == "setdefault" and node.args and _is_home_key(node.args[0]):
                add(name, node, "add")
            elif method == "update" and (
                    any(k.arg == "HOME" for k in node.keywords)
                    or any(isinstance(a, ast.Dict) and any(_is_home_key(k) for k in a.keys)
                           for a in node.args)):
                add(name, node, "add")
    for entries in events.values():
        entries.sort(key=lambda entry: entry[:2])
    return events


def _verdict_at(node, events: dict, at: tuple, seen: frozenset = frozenset()) -> str:
    """Whether `node`, evaluated at source position `at` in its function,
    carries HOME. See the scan's docstring for the judged shapes."""
    while isinstance(node, ast.Call) and getattr(node.func, "id", "") == "carry_clock_shift" \
            and node.args:
        node = node.args[0]
    if isinstance(node, ast.Constant) and node.value is None:
        return HAS
    if ast.unparse(node) in _ENVIRON:
        return HAS
    if isinstance(node, ast.Name):
        before = [e for e in events.get(node.id, ()) if e[:2] < at]
        binds = [i for i, e in enumerate(before) if e[2] == "bind"]
        if not binds:
            return UNKNOWN
        line, col, _kind, value = before[binds[-1]]
        if (node.id, line, col) in seen:
            return UNKNOWN
        verdict = _verdict_at(value, events, (line, col), seen | {(node.id, line, col)})
        for _line, _col, kind, _value in before[binds[-1] + 1:]:
            verdict = HAS if kind == "add" else LACKS
        return verdict
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        sides = {_verdict_at(node.left, events, at, seen),
                 _verdict_at(node.right, events, at, seen)}
        return HAS if HAS in sides else LACKS if sides == {LACKS} else UNKNOWN
    if isinstance(node, ast.Dict):
        if any(_is_home_key(k) for k in node.keys):
            return HAS
        if any(k is not None and not isinstance(k, ast.Constant) for k in node.keys):
            return UNKNOWN
        spreads = [v for k, v in zip(node.keys, node.values) if k is None]
        return HAS if any(_verdict_at(s, events, at, seen) == HAS for s in spreads) else LACKS
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
        if any(k.arg == "HOME" for k in node.keywords):
            return HAS
        parts = [*node.args[:1], *(k.value for k in node.keywords if k.arg is None)]
        verdicts = [_verdict_at(part, events, at, seen) for part in parts]
        if HAS in verdicts:
            return HAS
        return UNKNOWN if UNKNOWN in verdicts and node.args else LACKS
    return UNKNOWN


def _bare_runners(tree) -> set:
    """Local names bound to a subprocess runner by `from subprocess import ...`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name in _SUBPROCESS_RUNNERS)
    return names


def _env_judgements(tree) -> list:
    """(lineno, verdict) for every runner call's `env=` in a parsed module,
    each judged in the function (or module body) it sits in."""
    bare = _bare_runners(tree)
    scopes = [tree, *(n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    judged = []
    for scope in scopes:
        nodes = list(_local_nodes(scope))
        events = _name_events(nodes)
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not ((isinstance(func, ast.Attribute) and func.attr in _RUNNER_ATTRS)
                    or (isinstance(func, ast.Name) and func.id in bare)):
                continue
            for kw in node.keywords:
                if kw.arg == "env":
                    judged.append((node.lineno, _verdict_at(
                        kw.value, events, (node.lineno, node.col_offset))))
    return judged


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
    """Every `env=` a test passes to a subprocess runner carries HOME, as far as
    the shapes below can show. A child env without HOME finds its home through
    the password database: the real one.

    RUNNERS read: a call whose attribute is run, Popen, check_output,
    check_call, call, create_subprocess_exec or create_subprocess_shell (any
    receiver), and a bare name imported from subprocess (`from subprocess
    import run`, aliases included).

    JUDGED SHAPES (JUDGED_SHAPES; each is pinned to a verdict row). HAS:
    os.environ, dict(os.environ), os.environ.copy() (the conftest's sandbox
    HOME rides along); env=None (inherit); a dict literal or dict(...) with a
    HOME key or keyword; a spread, dict() argument or `|` operand that has HOME.
    LACKS: a dict literal or dict(...) with no HOME whose spreads and arguments
    show none (a spread of anything not shown to carry HOME counts as lacking
    it); a `|` of two lacking sides. A NAME is judged by its last binding
    (`=` or an annotated `x: T = v`) before the call in the same function, then
    by every later write before the call, in line order: `env["HOME"] = `,
    `env.update(HOME=...)` or `env.update({"HOME": ...})`, `env.setdefault("HOME",
    ...)` and `env |= {"HOME": ...}` make it HAS; `env.pop("HOME")` and
    `del env["HOME"]` make it LACKS. carry_clock_shift(x) is judged as x.

    EVERY OTHER SHAPE IS UNJUDGED AND PASSED. Examples: a helper call
    (`_env(root)`, `world.env()`); a name with no binding before the call in
    its function (a parameter, a global, a closure variable); an attribute or
    subscript (`w.env`, `calls[0][1]["env"]`); a comprehension over
    `os.environ.items()`; a mutation through another name or a function; the
    order of writes inside branches and loops (only line order is read); calls
    inside a lambda; and runners taking env positionally (os.execve, os.spawn*e,
    os.posix_spawn).
    """
    files = _test_source_files()
    misses = []
    for path in files:
        if "fixtures" in path.parts:
            continue
        tree = ast.parse(path.read_text(), str(path))
        misses += [f"{path.relative_to(_PLUGIN)}:{line}"
                   for line, verdict in _env_judgements(tree) if verdict == LACKS]
    assert len(files) > 100, files
    assert not sorted(set(misses)), sorted(set(misses))


def _verdict(source: str, scope_source: str = "") -> str:
    """The scan's verdict for one env expression, inside a function whose body
    is `scope_source`; it goes through _env_judgements, the scan's own path."""
    module = ast.parse(f"def probe(param):\n{textwrap.indent(scope_source or 'pass', '    ')}\n"
                       f"    subprocess.run([], env={source})\n")
    call_line = len(module.body[0].body) + 1
    found = [verdict for line, verdict in _env_judgements(module) if line == call_line]
    assert len(found) == 1, found
    return found[0]


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
    ("env", "base = {'PATH': '/bin'}\nenv = carry_clock_shift(base)", LACKS),
    # A name bound only after the use is unbound there.
    ("env", "env = carry_clock_shift(base)\nbase = {'PATH': '/bin'}", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv = dict(env, HOME='/h')", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv = dict(env, X='1')", LACKS),
    # HOME stripped from a name after it is built.
    ("env", "env = dict(os.environ)\nenv.pop('HOME', None)", LACKS),
    ("env", "env = dict(os.environ)\ndel env['HOME']", LACKS),
    ("carry_clock_shift(env)", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    # HOME stripped, then the name spread or copied: still lacking.
    ("{**env}", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    ("dict(env, X='1')", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    ("carry_clock_shift({**env})", "env = dict(os.environ)\ndel env['HOME']", LACKS),
    # HOME written after the dict is built.
    ("env", "env = {'PATH': '/bin'}\nenv['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update(HOME='/h')", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update({'HOME': '/h'})", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.setdefault('HOME', '/h')", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv |= {'HOME': '/h'}", HAS),
    ("env", "env = dict(os.environ)\nenv.pop('HOME')\nenv['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv['HOME'] = '/h'\nenv.pop('HOME')", LACKS),
    ("env", "env = {'PATH': '/bin'}\nenv['X'] = '1'", LACKS),
    # Unions and annotated assignments.
    ("{'X': '1'} | {'HOME': '/h'}", "", HAS),
    ("os.environ | {'X': '1'}", "", HAS),
    ("{'X': '1'} | {'Y': '2'}", "", LACKS),
    ("{'X': '1'} | param", "", UNKNOWN),
    ("env", "env: dict = {'PATH': '/bin'}", LACKS),
    ("env", "env: dict = dict(os.environ)", HAS),
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


@pytest.mark.parametrize("probe, flagged_lines", [
    # The scan's own strip check, through carry_clock_shift.
    ("def test_child():\n    env = dict(os.environ)\n    env.pop('HOME')\n"
     "    subprocess.run(['true'], env=carry_clock_shift(env))\n", [9]),
    # Runners imported by bare name, aliased or not, and asyncio's.
    ("from subprocess import run\n\n\ndef test_child():\n"
     "    run(['true'], env={'X': '1'})\n", [10]),
    ("from subprocess import Popen as launch\n\n\ndef test_child():\n"
     "    launch(['true'], env={'X': '1'})\n", [10]),
    ("async def test_child():\n"
     "    await asyncio.create_subprocess_exec('true', env={'X': '1'})\n", [7]),
    # Names judged per function: a parameter passed in one function is not
    # judged by an unrelated literal bound in another.
    ("def test_one(env):\n    subprocess.run(['true'], env=env)\n\n\n"
     "def test_two():\n    env = {'X': '1'}\n    return env\n", []),
    # Positional env is unjudged: os.execve and posix_spawn are not read.
    ("def test_child():\n    os.execve('/bin/true', ['true'], {'X': '1'})\n", []),
], ids=["strip-through-wrapper", "bare-run", "aliased-popen", "asyncio-exec",
        "per-function", "positional-env-unjudged"])
def test_the_whole_scan_judges_these_shapes(tmp_path, monkeypatch, probe, flagged_lines):
    """Through the real scan, not a copy of its rules."""
    _seeded_rig(tmp_path, monkeypatch, {
        "tests/test_zzshape.py": "import asyncio\nimport os\nimport subprocess\n\n\n" + probe})
    expected = [f"tests/test_zzshape.py:{line}" for line in flagged_lines]
    if expected:
        with pytest.raises(AssertionError) as raised:
            test_no_child_environment_built_in_a_test_leaves_out_home()
        assert repr(expected) in str(raised.value), str(raised.value)
    else:
        test_no_child_environment_built_in_a_test_leaves_out_home()


# One row per JUDGED_SHAPES entry: a module source and the verdict of its
# single runner call, through _env_judgements (the scan's own path).
_SHAPE_ROWS = {
    "environ": ("def t():\n    subprocess.run([], env=os.environ.copy())\n", HAS),
    "none": ("def t():\n    subprocess.run([], env=None)\n", HAS),
    "dict-literal": ("def t():\n    subprocess.run([], env={'X': '1'})\n", LACKS),
    "spread": ("def t(p):\n    subprocess.run([], env={**p})\n", LACKS),
    "dict-call": ("def t():\n    subprocess.run([], env=dict(os.environ, X='1'))\n", HAS),
    "union": ("def t():\n    subprocess.run([], env={'X': '1'} | {'Y': '2'})\n", LACKS),
    "name": ("def t():\n    e = {'X': '1'}\n    subprocess.run([], env=e)\n", LACKS),
    "annotated-binding": ("def t():\n    e: dict = {'X': '1'}\n    subprocess.run([], env=e)\n",
                          LACKS),
    "post-build-write": ("def t():\n    e = {'X': '1'}\n    e['HOME'] = '/h'\n"
                         "    subprocess.run([], env=e)\n", HAS),
    "strip": ("def t():\n    e = dict(os.environ)\n    e.pop('HOME')\n"
              "    subprocess.run([], env=e)\n", LACKS),
    "carry-clock-shift": ("def t():\n    e = {'X': '1'}\n"
                          "    subprocess.run([], env=carry_clock_shift(e))\n", LACKS),
    "bare-runner": ("from subprocess import run as go\n\n\ndef t():\n"
                    "    go([], env={'X': '1'})\n", LACKS),
    "asyncio-runner": ("async def t():\n"
                       "    await asyncio.create_subprocess_exec('x', env={'X': '1'})\n", LACKS),
}


def test_every_judged_shape_has_a_row():
    assert set(_SHAPE_ROWS) == set(JUDGED_SHAPES), (
        set(_SHAPE_ROWS) ^ set(JUDGED_SHAPES))


@pytest.mark.parametrize("shape", JUDGED_SHAPES)
def test_each_judged_shape_is_judged(shape):
    source, verdict = _SHAPE_ROWS[shape]
    assert [v for _, v in _env_judgements(ast.parse(source))] == [verdict]
