"""
Location: pact-plugin/tests/test_no_global_random_reseed.py
Summary: Refuses test code that reseeds the global random generator.
Used by: the suite.

A test that reseeds Python's global `random` generator, or numpy's, leaves that
state for every later test on the same worker. A test that needs a fixed
sequence uses a local generator instead (`random.Random(seed)`).

What it detects: a reference, called or not, to a global random-state setter
(SETTERS), in any tracked .py under tests/, the plugin-root conftest.py, and any
tracked test_*.py or conftest.py under skills/; an untracked file is not read. A
name resolves through the file's own
imports at any scope (`import random as r`, `from random import seed`, `import
numpy as np`), and a from-import of a setter counts too. A method of a local
generator (`random.Random(1).seed(2)`) is not a setter.

What it cannot see: a setter reached through getattr or a computed name; one
whose module is bound other than by an import (an assignment such as
`m = random`, or a parameter); a setter outside SETTERS; and a reseed inside a
non-test module or a library the tests import.
"""
import ast
import os
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_glob

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SETTERS = frozenset({"random.seed", "random.setstate", "numpy.random.seed",
                     "numpy.random.set_state"})


def _test_files(plugin_root, known_member=None):
    """The tracked test-side .py files the pin reads."""
    files = tracked_glob(plugin_root, "tests/**/*.py", known_member=known_member)
    files += tracked_glob(plugin_root, "conftest.py", allow_empty=True)
    files += tracked_glob(plugin_root, "skills/**/test_*.py", allow_empty=True)
    files += tracked_glob(plugin_root, "skills/**/conftest.py", allow_empty=True)
    return sorted(set(files))


def _aliases(tree):
    """{bound name: dotted module or object} for every import in `tree`."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                aliases[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _dotted(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def global_reseeds(source, where="<rig>"):
    """'where:line: setter' for every global random-state setter `source` refers to."""
    tree = ast.parse(source)
    aliases = _aliases(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found += [f"{where}:{node.lineno}: imports {node.module}.{alias.name}"
                      for alias in node.names if f"{node.module}.{alias.name}" in SETTERS]
        elif isinstance(node, (ast.Name, ast.Attribute)) and not isinstance(node.ctx, ast.Store):
            dotted = _dotted(node, aliases)
            if dotted in SETTERS:
                found.append(f"{where}:{node.lineno}: {dotted}")
    return found


def reseed_findings(plugin_root, known_member=None):
    """Every global reseed in the tracked test files under `plugin_root`."""
    found = []
    for path in _test_files(plugin_root, known_member):
        found += global_reseeds(path.read_text(encoding="utf-8"),
                                path.relative_to(plugin_root).as_posix())
    return found


def test_no_test_reseeds_the_global_random_generator():
    assert len(_test_files(PLUGIN_ROOT, Path(__file__))) > 100
    found = reseed_findings(PLUGIN_ROOT, Path(__file__))
    assert found == [], (
        "these reseed the global random generator and leave it for later tests; "
        f"use a local random.Random(seed): {found}")


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=60,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": os.devnull})


def test_an_untracked_file_is_not_read(tmp_path):
    """A scratch file beside the suite does not fail it, nor does a tracked file
    deleted from the tree; a tracked file that reseeds does."""
    root = tmp_path / "plugin"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_tracked.py").write_text("import random\nrandom.seed(1)\n")
    (root / "tests" / "test_clean.py").write_text("import random\nrandom.Random(1)\n")
    (root / "tests" / "test_gone.py").write_text("import random\nrandom.seed(2)\n")
    _git("init", "-q", cwd=root)
    _git("add", "tests", cwd=root)
    (root / "tests" / "test_gone.py").unlink()
    (root / "tests" / "zz_probe.py").write_text("import random\nrandom.seed(0)\n")
    assert reseed_findings(root) == ["tests/test_tracked.py:2: random.seed"]


@pytest.mark.parametrize("source", [
    "import random\nrandom.seed(1136)\n",
    "import random as r\nr.seed(1)\n",
    "from random import seed\nseed(1)\n",
    "from random import setstate as put\n",
    "import random\nrandom.setstate(random.getstate())\n",
    "import random\nreseed = random.seed\n",
    "def f():\n    import random\n    random.seed(2)\n",
    "import numpy as np\nnp.random.seed(0)\n",
    "from numpy import random as npr\nnpr.seed(0)\n",
    "import numpy.random\nnumpy.random.set_state(None)\n",
])
def test_a_global_reseed_is_refused(source):
    assert global_reseeds(source), source


@pytest.mark.parametrize("source", [
    "import random\nrng = random.Random(1136)\nrng.seed(2)\nrng.choice('ab')\n",
    "import random\nrandom.Random(1).seed(2)\n",
    "import random\nstate = random.getstate()\nrandom.random()\n",
    "from random import Random\nRandom(3).randint(0, 4)\n",
    "def seed(x):\n    return x\nseed(1)\n",
    "import numpy as np\nnp.random.default_rng(0).integers(4)\n",
])
def test_a_local_generator_or_a_same_named_helper_is_not_refused(source):
    assert global_reseeds(source) == [], source
