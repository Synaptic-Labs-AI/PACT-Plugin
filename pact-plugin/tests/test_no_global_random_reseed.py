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
name resolves through the imports of the scope that reads it (`import random as
r`, `from random import seed`, `import numpy as np`): the reading function's
own, then each enclosing function's, then the module's, with a class body's
imports seen only in that class body, as Python resolves names. A name one
scope imports more than once counts when any of those imports is a setter. A
from-import of a setter counts too. After `from random
import *` or `from numpy.random import *`, a bare setter name (`seed(1)`)
counts when the star import is the module-level binding of that name in force
where it is read: the last one above the read for a read at module level, and
the last one in the file for a read inside a function or lambda. A module-level
def, class, import or assignment target rebinds the name. A method of a local
generator (`random.Random(1).seed(2)`) is not a setter.

Over-refused on purpose, in a module that star-imports random or numpy.random:
a parameter or local variable named like a setter (`def f(seed): seed`), and a
rebinding the rule does not read (one inside a class body, an `except ... as`
clause, a `global` statement in a function, an assignment expression inside a
comprehension, or a star import from another module) leave the name read as
the setter. A comprehension's own loop variable is not a rebinding, and a read
of it inside the comprehension is not a read of the setter; the comprehension's
first iterable is read in the enclosing scope, before the variable exists.

What it cannot see: a setter reached through getattr or a computed name; one
whose module is bound other than by an import (an assignment such as
`m = random`, or a parameter); a star import from a module that re-exports
the setters (`from mylib import *` where mylib star-imports random); a setter
outside SETTERS; and a reseed inside a non-test module or a library the tests
import.
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


SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _scoped_reads(tree):
    """{id of a Name or Attribute read: dotted names it can stand for}, each name
    resolved through the imports of the scope that reads it."""
    imports = {}

    def collect(node, scope):
        for child in ast.iter_child_nodes(node):
            bound = imports.setdefault(id(scope), {})
            if isinstance(child, ast.Import):
                for alias in child.names:
                    top = alias.name.split(".")[0]
                    bound.setdefault(alias.asname or top, set()).add(
                        alias.name if alias.asname else top)
            elif isinstance(child, ast.ImportFrom) and child.module and not child.level:
                for alias in child.names:
                    if alias.name != "*":
                        bound.setdefault(alias.asname or alias.name, set()).add(
                            f"{child.module}.{alias.name}")
            collect(child, child if isinstance(child, SCOPES) else scope)

    collect(tree, tree)

    def lookup(name, chain):
        for depth, scope in enumerate(reversed(chain)):
            if depth and isinstance(scope, ast.ClassDef):
                continue
            if name in imports.get(id(scope), {}):
                return imports[id(scope)][name]
        return set()

    def dotted(node, chain):
        if isinstance(node, ast.Name):
            return lookup(node.id, chain)
        if isinstance(node, ast.Attribute):
            return {f"{base}.{node.attr}" for base in dotted(node.value, chain)}
        return set()

    reads = {}

    def visit(node, chain):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Name, ast.Attribute)) and not isinstance(child.ctx, ast.Store):
                reads[id(child)] = dotted(child, chain)
            visit(child, [*chain, child] if isinstance(child, SCOPES) else chain)

    visit(tree, [tree])
    return reads


COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _star_setters(tree):
    """{id of a Name read: setter} for each read a star import of random or
    numpy.random binds to a setter, by the module-level binding in force there."""
    bindings, reads = {}, []

    def bind(name, line, setter=None):
        bindings.setdefault(name, []).append((line, setter))

    def visit(node, deferred, own_scope, local):
        module_scope = not deferred and not own_scope
        if module_scope and isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    dotted = f"{node.module}.{alias.name}"
                    bind(alias.asname or alias.name, node.lineno, dotted if dotted in SETTERS else None)
                elif not node.level:
                    for setter in SETTERS:
                        module, _, name = setter.rpartition(".")
                        if module == node.module:
                            bind(name, node.lineno, setter)
        elif module_scope and isinstance(node, ast.Import):
            for alias in node.names:
                bind(alias.asname or alias.name.split(".")[0], node.lineno)
        elif module_scope and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(node.name, node.lineno)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id not in local:
                reads.append((node, deferred))
            elif module_scope:
                bind(node.id, node.lineno)
        inner_deferred = deferred or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        inner_scope = own_scope or isinstance(node, (ast.ClassDef, *COMPREHENSIONS))
        if not isinstance(node, COMPREHENSIONS):
            for child in ast.iter_child_nodes(node):
                visit(child, inner_deferred, inner_scope, local)
            return
        # The first iterable runs in the enclosing scope, before any loop
        # variable exists; everything else runs inside the comprehension.
        first = node.generators[0].iter
        visit(first, deferred, own_scope, local)
        inner = local | {name.id for loop in node.generators
                         for name in ast.walk(loop.target) if isinstance(name, ast.Name)}
        for child in ast.iter_child_nodes(node):
            parts = ast.iter_child_nodes(child) if isinstance(child, ast.comprehension) else [child]
            for part in parts:
                if part is not first:
                    visit(part, inner_deferred, inner_scope, inner)

    visit(tree, False, False, frozenset())
    found = {}
    for read, deferred in reads:
        seen = sorted(bindings.get(read.id, []), key=lambda binding: binding[0])
        in_force = seen if deferred else [b for b in seen if b[0] < read.lineno]
        if in_force and in_force[-1][1]:
            found[id(read)] = in_force[-1][1]
    return found


def global_reseeds(source, where="<rig>"):
    """'where:line: setter' for every global random-state setter `source` refers to."""
    tree = ast.parse(source)
    scoped = _scoped_reads(tree)
    starred = _star_setters(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found += [f"{where}:{node.lineno}: imports {node.module}.{alias.name}"
                      for alias in node.names if f"{node.module}.{alias.name}" in SETTERS]
        elif isinstance(node, (ast.Name, ast.Attribute)) and not isinstance(node.ctx, ast.Store):
            setters = {starred[id(node)]} if id(node) in starred else scoped.get(id(node), set()) & SETTERS
            found += [f"{where}:{node.lineno}: {setter}" for setter in sorted(setters)]
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
    "from random import *\nseed(1)\n",
    "from random import *\nsetstate(getstate())\n",
    "from numpy.random import *\nseed(0)\n",
    "from random import *\ndef f(seed):\n    return seed\n",
    "from random import *\nseed(1)\ndef seed(x):\n    return x\n",
    "def seed(x):\n    return x\nfrom random import *\nseed(1)\n",
    "from random import *\ndef f():\n    seed(1)\n",
    "from random import *\nfrom mylib import *\nseed(1)\n",
    "from random import *\nclass C:\n    seed = print\n    seed(1)\n",
    "from random import *\ntry:\n    pass\nexcept Exception as seed:\n    pass\nseed(1)\n",
    "from random import *\ndef f():\n    global seed\n    seed = print\nf()\nseed(1)\n",
    "from random import *\n[(seed := print) for _ in 'a']\nseed(1)\n",
    "from random import *\n[seed for seed in range(2)]\nseed(1)\n",
    "from random import *\nprint([0 for seed in range(2)], seed(1))\n",
    "from random import *\nprint([0 for seed in seed(1)])\n",
    "import random\ndef f():\n    import os as random\n    return random\nrandom.seed(1)\n",
    "import random\nclass C:\n    import os as random\nrandom.seed(1)\n",
    "import random\nclass C:\n    import os as random\n    def f(self):\n        random.seed(1)\n",
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
    "from random import *\ndef seed(x):\n    return x\nseed(1)\n",
    "from random import *\nRandom(1).seed(2)\n",
    "from random import *\nseed = print\nseed(1)\n",
    "from os import *\nseed(1)\n",
    "from random import *\ndef f():\n    seed(1)\ndef seed(x):\n    return x\n",
    "from random import *\nfrom mylib import seed\nseed(1)\n",
    "from random import *\nfor seed in range(2):\n    seed\n",
    "from mylib import *\nseed(1)\n",
    "from random import *\nprint([seed for seed in range(2)])\n",
    "import os as random\ndef f():\n    import random\n    return random\nrandom.seed(1)\n",
    "import random\ndef f():\n    import os as random\n    random.seed(1)\n",
])
def test_a_local_generator_or_a_same_named_helper_is_not_refused(source):
    assert global_reseeds(source) == [], source
