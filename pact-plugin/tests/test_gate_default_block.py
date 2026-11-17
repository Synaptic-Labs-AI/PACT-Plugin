"""
Location: pact-plugin/tests/test_gate_default_block.py
Summary: Pins the default block of pytest-randomly and the seed gate that backs it.

The merge gate runs in file order because the repository-root pyproject.toml
addopts carries `-p no:randomly`. Two edits would undo that silently in CI,
where the plugin is installed: deleting the line, or adding a nearer pytest
config file that pytest picks instead of the root one. pytest-randomly may or
may not be installed locally. Where it is absent, neither edit changes a local
run; where it is installed, the seed gate refuses the session, as in CI. The
static arms below red on both edits whether or not the plugin is installed; the
seed-gate arms pin the runtime refusal wherever the plugin is loaded.
"""

import ast
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.config.findpaths import locate_config

import child_pytest_census
import randomly_seed_gate
from fixtures.tracked_files import tracked_files
from helpers import env_without_pytest_vars

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PLUGIN_ROOT.parent
_ROOT_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _inifile(directory):
    """The config file pytest's own search picks for a run started in `directory`
    with `directory` as its path argument, or None.

    locate_config is pytest-internal: no public pytest API returns the chosen
    file without building a Config, which is internal too. Its return tuple is
    not a stable interface (pytest 9.2 returns four items), so this takes the one
    returned item that is an existing file, never an index."""
    files = [item for item in locate_config(directory, [directory])
             if isinstance(item, Path) and item.is_file()]
    assert len(files) <= 1, f"locate_config returned more than one file: {files}"
    return files[0] if files else None


def _addopts_words(pyproject):
    """The addopts of a pyproject's [tool.pytest.ini_options], as argv words."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    addopts = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get(
        "addopts", "")
    return shlex.split(addopts) if isinstance(addopts, str) else list(addopts)


def _blocks_randomly(words):
    """True when pytest, given `words` with autoload on and no PYTEST_PLUGINS,
    leaves pytest-randomly unloaded: the child-pytest census's own reader."""
    return child_pytest_census._deterministic(words, {})


def _tracked_directories():
    """pact-plugin/ and every directory under it that holds a tracked file."""
    files = tracked_files(_REPO_ROOT, "pact-plugin",
                          known_member="pact-plugin/tests/conftest.py")
    directories = {_PLUGIN_ROOT}
    for path in files:
        directories.update(parent for parent in Path(path).parents
                           if _PLUGIN_ROOT in parent.parents or parent == _PLUGIN_ROOT)
    return sorted(directories)


class TestRootConfigBlocksRandomly:

    def test_root_pyproject_addopts_blocks_randomly(self):
        words = _addopts_words(_ROOT_PYPROJECT)
        assert _blocks_randomly(words), (
            f"{_ROOT_PYPROJECT} [tool.pytest.ini_options] addopts is {words!r}; it "
            "must carry `-p no:randomly`, or every CI run shuffles in a fresh order")

    @pytest.mark.parametrize("words, blocked", [
        (["-p", "no:randomly"], True),
        (["-pno:randomly"], True),
        (["-ra", "-p", "no:randomly", "-q"], True),
        ([], False),
        (["-p", "no:cacheprovider"], False),
        (["-p", "no:randomly", "-p", "randomly"], False),
        (["-p", "randomly", "-p", "no:randomly"], True),
        (["-p", "no:randomly", "-p", "pytest_randomly"], False),
        (["-p", "no:randomly", "-ppytest_randomly"], False),
        (["-p", "no:randomly", "-p", "pytest_randomly.__init__"], False),
        (["-p", "no:randomly", "-prandomly"], False),
        (["-p", "pytest_randomly", "-p", "no:randomly"], True),
        (["-p", "no:pytest_randomly"], False),
    ])
    def test_the_block_reader_rows(self, words, blocked):
        assert _blocks_randomly(words) is blocked

    def test_the_reader_sees_a_module_name_unblock_in_addopts(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.pytest.ini_options]\naddopts = \"-p no:randomly -p pytest_randomly\"\n",
            encoding="utf-8")
        assert not _blocks_randomly(_addopts_words(pyproject))

    def test_the_reader_reads_a_pyproject_without_the_line(self, tmp_path):
        bare = tmp_path / "pyproject.toml"
        bare.write_text("[project]\nname = \"x\"\n", encoding="utf-8")
        assert not _blocks_randomly(_addopts_words(bare))


class TestNoNearerConfigShadowsTheRoot:

    def test_every_tracked_directory_resolves_the_root_pyproject(self):
        """pytest's own config search, from each directory a run could start in
        or name as its first path argument."""
        directories = _tracked_directories()
        assert _PLUGIN_ROOT / "tests" in directories
        shadowed = []
        for directory in directories:
            inifile = _inifile(directory)
            if inifile != _ROOT_PYPROJECT:
                shadowed.append(f"{directory.relative_to(_REPO_ROOT)} -> {inifile}")
        assert not shadowed, (
            "a pytest config file shadows the root pyproject.toml, so its "
            f"`-p no:randomly` does not apply there: {shadowed}")

    @pytest.mark.parametrize("name, text", [
        ("pytest.ini", "[pytest]\n"),
        ("pytest.toml", "[pytest]\n"),
        ("tox.ini", "[pytest]\n"),
        ("setup.cfg", "[tool:pytest]\n"),
        ("pyproject.toml", "[tool.pytest.ini_options]\nmarkers = []\n"),
    ])
    def test_the_search_sees_a_nearer_config(self, tmp_path, name, text):
        """The control: in a copy of the layout, each config name shadows."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts = \"-p no:randomly\"\n",
            encoding="utf-8")
        plugin = tmp_path / "pact-plugin" / "tests"
        plugin.mkdir(parents=True)
        assert _inifile(plugin) == tmp_path / "pyproject.toml"
        (tmp_path / "pact-plugin" / name).write_text(text, encoding="utf-8")
        assert _inifile(plugin) == tmp_path / "pact-plugin" / name

    @pytest.mark.parametrize("shape", ["rootdir, inifile, cfg", "rootdir, inifile, cfg, ignored"])
    def test_the_config_reader_takes_the_file_whatever_the_tuple_shape(
            self, tmp_path, monkeypatch, shape):
        """A three-item return and the four-item return of pytest 9.2 both give
        the file, and a search that finds none gives None."""
        inifile = tmp_path / "pyproject.toml"
        inifile.write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
        items = {"rootdir": tmp_path, "cfg": {}, "ignored": []}
        names = shape.split(", ")
        module = sys.modules[__name__]
        monkeypatch.setattr(module, "locate_config", lambda *_: tuple(
            {**items, "inifile": inifile}[name] for name in names))
        assert _inifile(tmp_path) == inifile
        monkeypatch.setattr(module, "locate_config", lambda *_: tuple(
            {**items, "inifile": None}[name] for name in names))
        assert _inifile(tmp_path) is None


def _configure_calls_the_gate(source):
    """True when the module-level pytest_configure in ``source`` itself calls
    ``randomly_seed_gate.refuse_unseeded(config)``, outside any function nested
    in it."""
    configure = next((node for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef) and node.name == "pytest_configure"),
                     None)
    if configure is None:
        return False
    stack = list(configure.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if (isinstance(node, ast.Call)
                and ast.unparse(node.func) == "randomly_seed_gate.refuse_unseeded"
                and [ast.unparse(arg) for arg in node.args] == ["config"]):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _config(args, loaded=True, worker=False, modules=()):
    """A config stub. `loaded` registers the plugin under its entry-point name;
    `modules` registers plugin objects under those module names, as
    `-p pytest_randomly` does."""
    plugins = [SimpleNamespace(__name__=name) for name in ("conftest", *modules)]
    config = SimpleNamespace(
        pluginmanager=SimpleNamespace(
            hasplugin=lambda name: loaded and name == "randomly",
            get_plugins=lambda: list(plugins)),
        invocation_params=SimpleNamespace(args=tuple(args)))
    if worker:
        config.workerinput = {}
    return config


class TestSeedGate:

    @pytest.mark.parametrize("args", [
        ["-p", "randomly", "--randomly-seed=7"],
        ["-p", "randomly", "--randomly-seed", "0"],
        ["-p", "randomly", "--randomly-seed=last", "--randomly-seed=123456789"],
        # Past a bare `--`: pytest applies the seed when no path precedes `--`.
        ["-p", "randomly", "--", "--randomly-seed=5"],
        # A path before `--` makes pytest read the word as a path and stop with
        # "file or directory not found"; the gate's verdict is pinned as is.
        ["-p", "randomly", "tests", "--", "--randomly-seed=5"],
        ["tests", "-p", "randomly", "--", "--randomly-seed=5"],
        # Every value pytest-randomly's int() parse accepts seeds the run (measured
        # with pytest-randomly 5.1.0: each runs with the seed int() gives).
        ["-p", "randomly", "--randomly-seed=+5"],
        ["-p", "randomly", "--randomly-seed=-5"],
        ["-p", "randomly", "--randomly-seed", " 5"],
        ["-p", "randomly", "--randomly-seed=5_0"],
        ["-p", "randomly", "--randomly-seed=007"],
        ["-p", "randomly", "--randomly-seed=\u0663"],
        ["-p", "randomly", f"--randomly-seed={2**70}"],
    ])
    def test_a_seeded_session_passes(self, args):
        randomly_seed_gate.refuse_unseeded(_config(args))

    @pytest.mark.parametrize("args", [
        [],
        ["-p", "randomly"],
        ["--randomly-seed=last"],
        ["--randomly-seed=default"],
        ["--randomly-seed="],
        ["--randomly-seed"],
        # Values int() refuses, as pytest-randomly does with a usage error.
        ["--randomly-seed=\u00b2"],
        ["--randomly-seed=5.0"],
        ["--randomly-seed=0x5"],
        ["--randomly-seed=_5"],
        ["--randomly-seed=5__0"],
        ["--randomly-seed=7", "--randomly-seed=last"],
        # pytest parses a post-`--` word as the option here and refuses `x`.
        ["-p", "randomly", "--randomly-seed=5", "--", "--randomly-seed=x"],
    ])
    def test_an_unseeded_session_is_refused(self, args):
        with pytest.raises(pytest.UsageError, match="names no integer --randomly-seed"):
            randomly_seed_gate.refuse_unseeded(_config(args))

    @pytest.mark.parametrize("module", ["pytest_randomly", "pytest_randomly.__init__"])
    def test_a_module_name_load_is_refused(self, module):
        """`-p pytest_randomly` registers the plugin under its module name, so
        hasplugin("randomly") is False while it shuffles."""
        with pytest.raises(pytest.UsageError, match="names no integer --randomly-seed"):
            randomly_seed_gate.refuse_unseeded(_config([], loaded=False, modules=(module,)))

    def test_a_seeded_module_name_load_passes(self):
        randomly_seed_gate.refuse_unseeded(_config(
            ["-p", "pytest_randomly", "--randomly-seed=7"], loaded=False,
            modules=("pytest_randomly",)))

    @pytest.mark.parametrize("module", ["pytest_randomly_extra", "my.pytest_randomly"])
    def test_a_similar_module_name_is_not_the_plugin(self, module):
        randomly_seed_gate.refuse_unseeded(_config([], loaded=False, modules=(module,)))

    def test_the_refusal_names_the_remedy(self):
        with pytest.raises(pytest.UsageError) as raised:
            randomly_seed_gate.refuse_unseeded(_config([]))
        assert randomly_seed_gate.REMEDY in str(raised.value)
        assert "-p no:randomly" in randomly_seed_gate.REMEDY

    def test_a_blocked_session_passes(self):
        randomly_seed_gate.refuse_unseeded(_config([], loaded=False))

    def test_a_worker_defers_to_the_controller(self):
        randomly_seed_gate.refuse_unseeded(_config([], worker=True))

    def test_the_plugin_root_conftest_calls_the_gate(self):
        """The gate only works if pytest_configure calls it, and only the
        plugin-root conftest loads for a session of skills-adjacent tests."""
        source = (_PLUGIN_ROOT / "conftest.py").read_text(encoding="utf-8")
        assert _configure_calls_the_gate(source), (
            "pact-plugin/conftest.py's pytest_configure must itself call "
            "randomly_seed_gate.refuse_unseeded(config)")

    @pytest.mark.parametrize("source, calls", [
        ("def pytest_configure(config):\n"
         "    randomly_seed_gate.refuse_unseeded(config)\n", True),
        ("def pytest_configure(config):\n    pass\n\n\n"
         "def later(config):\n    randomly_seed_gate.refuse_unseeded(config)\n", False),
        ("def pytest_configure(config):\n"
         "    def inner():\n        randomly_seed_gate.refuse_unseeded(config)\n", False),
        ("def pytest_configure(config):\n"
         "    randomly_seed_gate.refuse_unseeded(None)\n", False),
    ], ids=["in-the-body", "in-a-later-function", "in-a-nested-function", "another-argument"])
    def test_the_call_check_reads_only_pytest_configures_own_body(self, source, calls):
        assert _configure_calls_the_gate(source) is calls, source

    @pytest.mark.parametrize("fake_loaded", [True, False], ids=["loaded", "control"])
    def test_a_session_of_skills_adjacent_tests_alone_is_gated(self, tmp_path, fake_loaded):
        """A session that collects only a skills/ test never loads
        tests/conftest.py, so the gate must run from the plugin-root conftest.
        A stand-in plugin whose module name is pytest-randomly's makes the gate
        see the plugin loaded whether or not the real one is installed; the real
        one, where present, stays blocked by `-p no:randomly`."""
        (tmp_path / "zz_fake_randomly.py").write_text(
            '__name__ = "pytest_randomly.stand_in"\n', encoding="utf-8")
        skills_test = sorted(_PLUGIN_ROOT.glob("skills/*/test_*.py"))[0]
        plugin = ["-p", "zz_fake_randomly"] if fake_loaded else []
        # No PYTEST_* name from the shell that runs the suite reaches the child.
        env = env_without_pytest_vars()
        env.update(PYTHONPATH=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider",
             *plugin, "--co", "-q", str(skills_test)],
            cwd=_PLUGIN_ROOT, env=env, capture_output=True, text=True, timeout=120)
        output = run.stdout + run.stderr
        if fake_loaded:
            assert run.returncode == 4 and "names no integer --randomly-seed" in output, output
        else:
            assert run.returncode == 0 and "names no integer" not in output, output
