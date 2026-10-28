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

_PLUGIN = Path(__file__).resolve().parent.parent
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


def test_no_child_environment_built_in_a_test_leaves_out_home():
    runners = {"run", "Popen", "check_output", "check_call", "call"}
    files = list(_PLUGIN.glob("tests/**/*.py")) + list(_PLUGIN.glob("skills/*/test_*.py"))
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
