"""
Location: pact-plugin/tests/randomly_seed_gate.py
Summary: Fails a pytest session that loaded pytest-randomly without an explicit
         integer seed.
Used by: tests/conftest.py, whose pytest_configure calls refuse_unseeded();
         tests/test_gate_default_block.py.

pytest-randomly is installed in the CI environment and blocked by the
repository-root pyproject.toml addopts (`-p no:randomly`). A seeded diagnostic
run re-enables it with `-p randomly --randomly-seed=N`. A session that has it
loaded and names no seed shuffles in a fresh order every run. That happens when
the addopts line is lost, or when a nearer pytest config file shadows the root
one. It also happens when the plugin is loaded by its module name
(`-p pytest_randomly`), which the `-p no:randomly` block does not stop. xdist
workers inherit the controller's verdict, so they are skipped.

The seed must appear in the invocation args. A seed supplied only through
PYTEST_ADDOPTS or a config file is refused: the CI run line is the record of how
a run was ordered.
"""

import pytest

REMEDY = (
    "Restore `addopts = \"-p no:randomly\"` under [tool.pytest.ini_options] in "
    "the repository-root pyproject.toml, remove any nearer pytest config file "
    "that shadows it, or pass `-p randomly --randomly-seed=<int>`."
)


def _is_seed(value):
    return value.isascii() and value.isdigit()


def explicit_seed(args):
    """True when the last --randomly-seed in the args has an integer value.

    The last one counts because it is the one argparse keeps.
    """
    verdict = False
    for index, word in enumerate(args):
        if word.startswith("--randomly-seed="):
            verdict = _is_seed(word.partition("=")[2])
        elif word == "--randomly-seed":
            verdict = index + 1 < len(args) and _is_seed(args[index + 1])
    return verdict


def is_randomly_module(name):
    """True for pytest-randomly's own module, by any import name."""
    return name == "pytest_randomly" or name.startswith("pytest_randomly.")


def randomly_loaded(pluginmanager):
    """True when pytest-randomly is registered under its entry-point name or
    under its module name (`-p pytest_randomly` registers it there)."""
    if pluginmanager.hasplugin("randomly"):
        return True
    return any(is_randomly_module(getattr(plugin, "__name__", None) or "")
               for plugin in pluginmanager.get_plugins())


def refuse_unseeded(config):
    """Raise pytest.UsageError when pytest-randomly is loaded unseeded."""
    if hasattr(config, "workerinput"):
        return
    if not randomly_loaded(config.pluginmanager):
        return
    if explicit_seed(list(config.invocation_params.args)):
        return
    raise pytest.UsageError(
        "pytest-randomly is loaded but this invocation names no integer "
        "--randomly-seed, so the test order would change on every run. " + REMEDY
    )
