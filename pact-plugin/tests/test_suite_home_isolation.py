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
    "print(os.environ.get('CLAUDE_CONFIG_DIR'))\n"
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


def _env_lacks_home(node: ast.expr) -> bool:
    """Whether an `env=` value is a dict built here without HOME, which leaves the
    child to find its home through the password database: the real one."""
    while isinstance(node, ast.Call) and getattr(node.func, "id", "") == "carry_clock_shift":
        node = node.args[0]
    if isinstance(node, ast.Dict):
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        return None not in keys and "HOME" not in keys
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict" and not node.args:
        keys = [k.arg for k in node.keywords]
        return None not in keys and "HOME" not in keys
    return False


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
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "_PLUGIN", plugin)

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
            for node in ast.walk(scope):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in runners):
                    continue
                for kw in node.keywords:
                    if kw.arg != "env":
                        continue
                    value = kw.value
                    sources = built.get(value.id, []) if isinstance(value, ast.Name) else [value]
                    if sources and all(_env_lacks_home(v) for v in sources):
                        misses.append(f"{path.relative_to(_PLUGIN)}:{node.lineno}")
    assert len(files) > 100, files
    assert not sorted(set(misses)), sorted(set(misses))
