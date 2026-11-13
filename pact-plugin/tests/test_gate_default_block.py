"""
Location: pact-plugin/tests/test_gate_default_block.py
Summary: Pins the default block of pytest-randomly and the seed gate that backs it.

The merge gate runs in file order because the repository-root pyproject.toml
addopts carries `-p no:randomly`. Two edits would undo that silently in CI,
where the plugin is installed: deleting the line, or adding a nearer pytest
config file that pytest picks instead of the root one. The shared venv lacks the
plugin, so neither edit changes a local run. The static arms below red on both
edits without the plugin; the seed-gate arms pin the runtime refusal that fires
in CI.
"""

import shlex
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.config.findpaths import locate_config

import randomly_seed_gate
from fixtures.tracked_files import tracked_files

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PLUGIN_ROOT.parent
_ROOT_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _addopts_words(pyproject):
    """The addopts of a pyproject's [tool.pytest.ini_options], as argv words."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    addopts = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get(
        "addopts", "")
    return shlex.split(addopts) if isinstance(addopts, str) else list(addopts)


def _blocks_randomly(words):
    """True when the last -p naming pytest-randomly blocks it.

    `-p randomly` and `-p pytest_randomly` (or a submodule) each load it; only
    `-p no:randomly` blocks it, since the entry point is named `randomly`.
    """
    state = False
    for index, word in enumerate(words):
        value = (words[index + 1] if word == "-p" and index + 1 < len(words)
                 else word[2:] if word.startswith("-p") else None)
        if value == "no:randomly":
            state = True
        elif value == "randomly" or randomly_seed_gate.is_randomly_module(value or ""):
            state = False
    return state


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
            _rootdir, inifile, _cfg, _ignored = locate_config(directory, [directory])
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
        assert locate_config(plugin, [plugin])[1] == tmp_path / "pyproject.toml"
        (tmp_path / "pact-plugin" / name).write_text(text, encoding="utf-8")
        assert locate_config(plugin, [plugin])[1] == tmp_path / "pact-plugin" / name


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
        ["--randomly-seed=٣"],
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

    def test_conftest_calls_the_gate(self):
        """The gate only works if pytest_configure calls it."""
        source = (_PLUGIN_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        configure = source[source.index("def pytest_configure(config):"):]
        configure = configure[:configure.index("\ndef ", 1)]
        assert "randomly_seed_gate.refuse_unseeded(config)" in configure
