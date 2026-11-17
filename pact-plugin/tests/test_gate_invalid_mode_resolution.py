"""Coder-flagged item: the get_enum ADDITIVE stderr WARN (emitted when a *_MODE
env var is SET-but-invalid) must never CHANGE a gate's disposition, and must
never leak into the gate's stdout decision JSON.

WHAT THIS FILE CLAIMS, restated once more because the behaviour moved twice:
`PACT_DISPATCH_VARIETY_MODE` ships an ENFORCING default ("deny") but declares
`invalid_fallback = "warn"`, so an UNSET variable enforces while an UNPARSEABLE
one does not. Those are different user intents and the resolver now treats them
as such. The invariant is that resolution lands on the option's DECLARED
landing point for the path taken -- `default` for unset, `invalid_fallback` for
unrecognised -- and nowhere else, with the diagnostic on stderr.

Mechanism the tests pin:
- A set-but-invalid mode (e.g. "banana") resolves to that option's declared
  `invalid_fallback` -- "warn" for BOTH rows -- and never to anything derived
  from the invalid token.
- An UNSET variable resolves to that option's `default` -- "warn" for the
  inline-mission gate, "deny" for the dispatch-variety gate. **This is the arm
  carrying the discrimination**, because the invalid column coincides across
  the two rows and therefore cannot distinguish them.
  (TestInvalidModeResolvesToTheDeclaredFallback)
- The additive diagnostic is written to STDERR at gate import, so it cannot
  corrupt the gate's STDOUT decision JSON. (same class -- captures the reload
  stderr)
- Behaviorally, driving the REAL handoff_ordering_gate.main() while the invalid
  mode is active emits valid, uncorrupted JSON. (TestInvalidModeStdoutStaysClean)

Reload discipline: both gate modules resolve their mode constant at IMPORT, so we
reload under the invalid env and, after EVERY test, restore the mode variables to
their values from before that test and reload again, so no test hands a
test-chosen mode to the next; the module teardown does the same for the values
from before this module ran. Neither evicts from sys.modules -- sibling test
files import + reload them and would break if they vanished. Mirrors
test_pact_config_gate_migration. (TestTeardownRestoresAmbientModes pins the
restore.)
"""
import ast
import contextlib
import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

from fixtures.gate_reload import restored_env_and_reloaded

INLINE_ENV = "PACT_DISPATCH_INLINE_MISSION_MODE"
VARIETY_ENV = "PACT_DISPATCH_VARIETY_MODE"

# (module, env var, mode-constant name, UNSET default, INVALID fallback).
# Both are independent literals, not read back from the registry -- reading the
# registry would make the assertions tautological.
#
# 🔴 THE TWO COLUMNS ARE WHY THIS TABLE HAS FIVE ELEMENTS, and dropping either
# would produce a test that looks like per-option coverage and is not. After
# `invalid_fallback` landed, the INVALID column COINCIDES for both rows (both
# "warn"), so an invalid-only assertion expects the same answer for both gates
# and cannot tell them apart -- it would pass unchanged if one row's policy were
# deleted. The discrimination lives in the UNSET column, where they differ
# ("warn" vs "deny"). Verified by execution before this table was restructured,
# not reasoned about: an assertion whose two arms expect the same value is not
# a parametrisation, it is one arm run twice.
_GATES = [
    ("dispatch_gate", INLINE_ENV, "INLINE_MISSION_MODE", "warn", "warn"),
    ("handoff_ordering_gate", VARIETY_ENV, "DISPATCH_VARIETY_MODE", "deny", "warn"),
]


_GATE_MODULES = ("dispatch_gate", "handoff_ordering_gate")


@pytest.fixture(scope="module", autouse=True)
def _restore_gate_modules():
    """After this file, restore both mode variables to the values they had
    before it ran (set or absent), then reload both gates, so later tests see
    the constants they saw before this module. Do NOT evict from sys.modules."""
    with restored_env_and_reloaded((INLINE_ENV, VARIETY_ENV), _GATE_MODULES):
        yield


@pytest.fixture(autouse=True)
def _reload_gates_after_each_test():
    """After each test, restore both mode variables to their values before it
    and reload both gates, so the next test never sees a test-chosen mode."""
    with restored_env_and_reloaded((INLINE_ENV, VARIETY_ENV), _GATE_MODULES):
        yield


def _reload_capturing_stderr(monkeypatch, module_name, env_name, value, const_name):
    """Set env=value, reload module capturing import-time stderr, return
    (mode_constant, stderr_text)."""
    monkeypatch.setenv(env_name, value)
    module = importlib.import_module(module_name)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        importlib.reload(module)
    return getattr(module, const_name), buf.getvalue()


class TestInvalidModeResolvesToTheDeclaredFallback:
    @pytest.mark.parametrize(
        "module_name,env_name,const_name,unset_default,invalid_fallback", _GATES)
    def test_invalid_value_resolves_to_the_declared_invalid_fallback(
        self, monkeypatch, module_name, env_name, const_name,
        unset_default, invalid_fallback,
    ):
        mode, _stderr = _reload_capturing_stderr(
            monkeypatch, module_name, env_name, "banana", const_name
        )
        assert mode == invalid_fallback, (
            f"{module_name}: an invalid {env_name} must resolve to that "
            f"option's DECLARED invalid fallback {invalid_fallback!r}. It must "
            f"never resolve to the invalid token or to a value the resolver "
            f"invented -- the diagnostic reports the fallback, it does not "
            f"choose it."
        )

    @pytest.mark.parametrize(
        "module_name,env_name,const_name,unset_default,invalid_fallback", _GATES)
    def test_UNSET_resolves_to_the_declared_default_not_the_fallback(
        self, monkeypatch, module_name, env_name, const_name,
        unset_default, invalid_fallback,
    ):
        """THE ARM THAT CARRIES THE DISCRIMINATION. The invalid column
        coincides across both rows, so the test above cannot distinguish them;
        this one can, because the unset column differs ("warn" vs "deny").

        It is also the pin on the separation itself: unset and invalid are
        DIFFERENT user intents and must not collapse. For the variety row the
        two values differ, so a change that made either branch resolve like the
        other reddens here."""
        monkeypatch.delenv(env_name, raising=False)
        module = importlib.import_module(module_name)
        importlib.reload(module)
        assert getattr(module, const_name) == unset_default, (
            f"{module_name}: an UNSET {env_name} must resolve to the registry "
            f"default {unset_default!r}, never to the invalid fallback "
            f"{invalid_fallback!r} -- an absent variable is consent to the "
            f"shipped posture, not an unparseable request."
        )

    def test_the_two_options_actually_DIFFER_on_unset(self):
        """NON-VACUITY FOR THE PAIRING. If both rows ever declared the same
        unset default, every assertion in this class would hold with the rows
        swapped, and the parametrisation would be decoration. This asserts the
        table itself still discriminates."""
        unset_defaults = {row[3] for row in _GATES}
        assert len(unset_defaults) == len(_GATES), (
            "the _GATES rows no longer differ on their unset default, so the "
            "parametrised assertions above can no longer tell the two options "
            "apart -- restore a discriminating column or drop the claim"
        )

    @pytest.mark.parametrize(
        "module_name,env_name,const_name,unset_default,invalid_fallback", _GATES)
    def test_additive_warn_goes_to_stderr_not_stdout(
        self, monkeypatch, module_name, env_name, const_name,
        unset_default, invalid_fallback,
    ):
        # The tell: the invalid branch fires the diagnostic, and it lands on
        # STDERR (so it cannot pollute the gate's stdout decision JSON).
        _mode, stderr = _reload_capturing_stderr(
            monkeypatch, module_name, env_name, "banana", const_name
        )
        assert env_name in stderr and "banana" in stderr, (
            f"{module_name}: the additive get_enum WARN for an invalid {env_name} "
            f"must be emitted to stderr (the non-vacuity tell it resolved via the "
            f"invalid branch)"
        )


class TestInvalidModeStdoutStaysClean:
    """Behavioral: with the invalid mode active, real main() still emits valid,
    parseable JSON on stdout and exits 0 on a frame the gate has no opinion
    about.

    WHAT THIS ARM CAN AND CANNOT SHOW, because the frame is doing the work: a
    non-TaskUpdate frame cannot reach EITHER branch's advisory, so it could not
    deny under any mode — this arm therefore proves the stderr diagnostic does
    not corrupt stdout and does not crash the gate. It is NOT evidence that the
    gate never denies; that question is settled by the mode constant asserted
    above, and the dispatch-variety gate's answer to it is now 'it does'."""

    def test_handoff_gate_main_stdout_is_uncorrupted_under_invalid_mode(
        self, monkeypatch
    ):
        monkeypatch.setenv(VARIETY_ENV, "banana")
        gate = importlib.import_module("handoff_ordering_gate")
        with contextlib.redirect_stderr(io.StringIO()):
            importlib.reload(gate)  # "banana" -> the option's declared default
        benign = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(benign)))
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), pytest.raises(SystemExit) as exc:
            gate.main()
        assert exc.value.code == 0, (
            "a frame the gate has no opinion about must pass through at exit 0, "
            "whatever the resolved mode"
        )
        stdout = out_buf.getvalue().strip()
        parsed = json.loads(stdout)  # must be valid JSON (stderr WARN did not leak in)
        assert "permissionDecision" not in json.dumps(parsed), (
            "a pass-through frame must not carry a permissionDecision, and the "
            "additive stderr WARN must not surface in the stdout payload"
        )


def _restore_fixture(tree):
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_restore_gate_modules")


_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def _gate_import_reads():
    """{hook module: mode variables it resolves at import}, from every
    ``get_enum("NAME")`` a hooks/*.py file calls at module scope (try and if
    bodies included, function and class bodies not). A reload has to redo exactly
    these reads under the restored environment."""
    reads = {}
    for path in sorted(_HOOKS_DIR.glob("*.py")):
        stack = list(ast.parse(path.read_text(encoding="utf-8")).body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", None)) \
                if isinstance(node, ast.Call) else None
            if name == "get_enum" and node.args and isinstance(node.args[0], ast.Constant):
                reads.setdefault(path.stem, set()).add(node.args[0].value)
            stack.extend(ast.iter_child_nodes(node))
    return reads


def _strings(node, constants, seen=()):
    """The strings a literal, a tuple or list of them, or a module-level constant
    naming one, spells; None when any part cannot be read statically."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name) and node.id in constants and node.id not in seen:
        return _strings(constants[node.id], constants, (*seen, node.id))
    if isinstance(node, (ast.Tuple, ast.List)):
        parts = [_strings(element, constants, seen) for element in node.elts]
        return None if None in parts else set().union(*parts)
    return None


def _helper_arguments(tree, fixture):
    """(variables, modules) for each ``with restored_env_and_reloaded(...)`` in
    ``fixture``, read through the module's own constants."""
    constants = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name)}
    return [(_strings(item.context_expr.args[0], constants),
             _strings(item.context_expr.args[1], constants))
            for node in ast.walk(fixture) if isinstance(node, ast.With)
            for item in node.items
            if isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "id", None) == "restored_env_and_reloaded"
            and len(item.context_expr.args) == 2]


def _reloads_every_gate_read(tree, fixture):
    """True when a helper call in ``fixture`` names every mode variable and every
    module in _gate_import_reads(), so a restore cannot skip a gate or a variable."""
    reads = _gate_import_reads()
    variables, modules = set().union(*reads.values()), set(reads)
    return any(named_variables is not None and named_modules is not None
               and named_variables >= variables and named_modules >= modules
               for named_variables, named_modules in _helper_arguments(tree, fixture))


def _yields_inside_helper(fixture):
    """True when ``fixture`` yields inside a ``with restored_env_and_reloaded(...)``."""
    return any(
        isinstance(node, ast.With)
        and any(isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "restored_env_and_reloaded"
                for item in node.items)
        and any(isinstance(inner, ast.Yield)
                for statement in node.body for inner in ast.walk(statement))
        for node in ast.walk(fixture))


def _environ_writes(tree, fixture, module_name=None):
    """Line numbers where ``fixture`` writes the process environment, under any
    name the module binds os or os.environ to: ``import os as o``, ``from os
    import environ [as e]``, or a plain assignment such as ``env = os.environ``.

    A write is a mutating call on environ (pop, popitem, clear, update,
    setdefault), a subscript assignment or delete, ``environ |= ...``, an
    assignment to ``os.environ`` itself, or a call to ``os.putenv`` or
    ``os.unsetenv`` (also from-imported). A call from the fixture to a function
    defined in the same file counts when that function writes, one level deep,
    and is reported at the call's line in the fixture. The calls followed are
    ``f()`` for a module-level function, ``m.f()`` where ``m`` is this file
    imported by ``import <module_name> [as m]``, ``C.f()`` for a module-level
    class ``C``, and ``self.f()`` for a method of the class that holds the
    fixture, read as the fixture's first parameter.

    Not seen: writes through monkeypatch.setenv or delenv; a helper two or more
    calls deep, or one defined in another module; a method inherited from a
    base class, or reached through super(); a function or method reached
    through getattr, sys.modules, a dotted import path or a from-import of this
    file; and os or os.environ reached through getattr, sys.modules or a
    parameter."""
    os_names = {"os"} | {alias.asname for node in ast.walk(tree)
                         if isinstance(node, ast.Import)
                         for alias in node.names if alias.name == "os" and alias.asname}
    from_os = {alias.asname or alias.name: alias.name for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module == "os"
               for alias in node.names}
    environ_names = {name for name, imported in from_os.items() if imported == "environ"}
    env_setters = {name for name, imported in from_os.items()
                   if imported in ("putenv", "unsetenv")}

    def is_os_environ(node):
        return (isinstance(node, ast.Attribute) and node.attr == "environ"
                and isinstance(node.value, ast.Name) and node.value.id in os_names)

    def is_environ(node):
        return (isinstance(node, ast.Name) and node.id in environ_names) or is_os_environ(node)

    while True:
        rebound = {target.id for node in ast.walk(tree)
                   if isinstance(node, ast.Assign) and is_environ(node.value)
                   for target in node.targets if isinstance(target, ast.Name)}
        if rebound <= environ_names:
            break
        environ_names |= rebound

    def writes(node):
        if isinstance(node, ast.Call):
            func = node.func
            return (isinstance(func, ast.Attribute) and (
                (func.attr in ("pop", "popitem", "clear", "update", "setdefault")
                 and is_environ(func.value))
                or (func.attr in ("putenv", "unsetenv") and isinstance(func.value, ast.Name)
                    and func.value.id in os_names))
            ) or (isinstance(func, ast.Name) and func.id in env_setters)
        if isinstance(node, ast.AugAssign):
            return is_environ(node.target)
        if isinstance(node, (ast.Assign, ast.Delete)):
            return any((isinstance(target, ast.Subscript) and is_environ(target.value))
                       or is_os_environ(target)
                       for target in node.targets)
        return False

    def functions(body):
        return {node.name: node for node in body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fixture}

    helpers = functions(tree.body)
    classes = {node.name: functions(node.body) for node in tree.body if isinstance(node, ast.ClassDef)}
    own = next((node for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and fixture in node.body), None)
    receiver = fixture.args.args[0].arg if own is not None and fixture.args.args else None
    this_file = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names if module_name and alias.name == module_name}

    def target(call):
        func = call.func
        if isinstance(func, ast.Name):
            return helpers.get(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner = func.value.id
            if owner in this_file:
                return helpers.get(func.attr)
            if owner == receiver:
                return functions(own.body).get(func.attr)
            return classes.get(owner, {}).get(func.attr)
        return None

    return sorted({node.lineno for node in ast.walk(fixture) if writes(node) or (
        isinstance(node, ast.Call) and (helper := target(node)) is not None
        and any(writes(inner) for inner in ast.walk(helper))
    )})


class TestTeardownRestoresAmbientModes:
    """The module teardown helper puts each mode variable back as it found it.

    An operator's shell or a CI job may export a mode variable. Popping it at
    teardown would change the gate constants for every later test on the
    worker, so the helper must restore a set value and keep an absent one
    absent, then reload so the constant matches.
    """

    def test_an_absent_value_set_inside_is_removed(self, monkeypatch):
        monkeypatch.delenv(INLINE_ENV, raising=False)
        with contextlib.redirect_stderr(io.StringIO()):
            with restored_env_and_reloaded((INLINE_ENV,), ("dispatch_gate",)):
                os.environ[INLINE_ENV] = "deny"
        assert INLINE_ENV not in os.environ
        assert importlib.import_module("dispatch_gate").INLINE_MISSION_MODE == "warn"

    def test_an_ambient_value_removed_inside_comes_back(self, monkeypatch):
        monkeypatch.setenv(INLINE_ENV, "deny")
        with contextlib.redirect_stderr(io.StringIO()):
            with restored_env_and_reloaded((INLINE_ENV,), ("dispatch_gate",)):
                os.environ.pop(INLINE_ENV)
        assert os.environ.get(INLINE_ENV) == "deny"
        assert importlib.import_module("dispatch_gate").INLINE_MISSION_MODE == "deny"

    @pytest.mark.parametrize("module_file", [
        "test_gate_invalid_mode_resolution.py",
        "test_pact_config_gate_migration.py",
    ])
    def test_each_gate_module_teardown_restores_through_the_helper(self, module_file):
        """Both module fixtures yield inside the helper and never write
        os.environ themselves, so a body reverted to popping the mode
        variables fails here even though no test observes a module teardown."""
        tree = ast.parse((Path(__file__).parent / module_file).read_text(encoding="utf-8"))
        fixture = _restore_fixture(tree)
        assert _yields_inside_helper(fixture), (
            f"{module_file}: the fixture must yield inside restored_env_and_reloaded")
        assert _reloads_every_gate_read(tree, fixture), (
            f"{module_file}: the helper must name every gate mode variable and module "
            f"{_gate_import_reads()}, but names {_helper_arguments(tree, fixture)}")
        writes = _environ_writes(tree, fixture, Path(module_file).stem)
        assert not writes, f"{module_file}: the fixture writes os.environ at lines {writes}"

    @pytest.mark.parametrize("module_file", [
        "test_gate_invalid_mode_resolution.py",
        "test_pact_config_gate_migration.py",
    ])
    def test_each_gate_module_reloads_the_gates_after_every_test(self, module_file):
        """Both modules restore the mode variables and reload the gates after
        EVERY test, through a function-scoped autouse fixture, so no test hands
        a test-chosen mode to the next. Holds in any test order."""
        tree = ast.parse((Path(__file__).parent / module_file).read_text(encoding="utf-8"))
        fixture = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and node.name == "_reload_gates_after_each_test"), None)
        assert fixture is not None, f"{module_file}: no _reload_gates_after_each_test fixture"
        decorators = [d for d in fixture.decorator_list if isinstance(d, ast.Call)
                      and ast.unparse(d.func) == "pytest.fixture"]
        keywords = {k.arg: ast.literal_eval(k.value) for d in decorators for k in d.keywords}
        assert keywords.get("autouse") is True and keywords.get("scope", "function") == "function", (
            f"{module_file}: the fixture must be autouse and function-scoped: {keywords}")
        assert _yields_inside_helper(fixture), (
            f"{module_file}: the fixture must yield inside restored_env_and_reloaded")
        assert _reloads_every_gate_read(tree, fixture), (
            f"{module_file}: the helper must name every gate mode variable and module "
            f"{_gate_import_reads()}, but names {_helper_arguments(tree, fixture)}")
        assert not _environ_writes(tree, fixture, Path(module_file).stem), (
            f"{module_file}: the fixture writes os.environ")

    def test_the_gate_reads_the_pins_compare_against_are_real(self):
        """The variables and modules the two fixture pins require come from the
        gates' own import-time reads, and include this file's two mode variables,
        so the pins cannot pass against an empty requirement."""
        reads = _gate_import_reads()
        assert {INLINE_ENV, VARIETY_ENV} <= set().union(*reads.values()), reads

    @pytest.mark.parametrize("source", [
        "from os import environ\n"
        "def _restore_gate_modules():\n    yield\n    environ.pop('X', None)\n",
        "from os import environ as env\n"
        "def _restore_gate_modules():\n    yield\n    env['X'] = 'warn'\n",
        "import os as o\n"
        "def _restore_gate_modules():\n    yield\n    del o.environ['X']\n",
        "import os\n"
        "def _restore_gate_modules():\n    env = os.environ\n    env.update(X='warn')\n    yield\n",
    ], ids=["from-import", "from-import-as", "import-as", "local-rebinding"])
    def test_the_write_check_sees_an_aliased_environ(self, source):
        """The pin above must not be evaded by reaching os.environ under
        another name."""
        tree = ast.parse(source)
        assert _environ_writes(tree, _restore_fixture(tree)) == [4]

    @pytest.mark.parametrize("source", [
        "import os\n"
        "def _restore_gate_modules():\n    yield\n    os.environ |= {'X': 'warn'}\n",
        "import os\n"
        "def _restore_gate_modules():\n    yield\n    os.environ = {}\n",
        "import os\n"
        "def _restore_gate_modules():\n    yield\n    os.putenv('X', 'warn')\n",
        "import os\n"
        "def _restore_gate_modules():\n    yield\n    os.unsetenv('X')\n",
        "from os import unsetenv as drop\n"
        "def _restore_gate_modules():\n    yield\n    drop('X')\n",
        "import os\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules():\n    _set()\n    yield\n",
        "import os\nimport rig as m\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules():\n    m._set()\n    yield\n",
        "import os\n"
        "class T:\n    @staticmethod\n    def _set():\n        os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules():\n    T._set()\n    yield\n",
        "import os\n"
        "class T:\n    def _set(self):\n        os.environ['X'] = 'warn'\n"
        "    def _restore_gate_modules(self):\n        self._set()\n        yield\n",
    ], ids=["or-equals", "rebinding-os-environ", "putenv", "unsetenv",
            "from-imported-unsetenv", "helper-one-level", "helper-through-this-module",
            "helper-through-a-module-class", "helper-through-self"])
    def test_the_write_check_sees_each_write_form(self, source):
        """Each form changes the process environment, so each must count, at the
        fixture's own line for a helper's write."""
        tree = ast.parse(source)
        fixture = _restore_fixture(tree)
        expected = [fixture.lineno + (1 if "_set" in source else 2)]
        assert _environ_writes(tree, fixture, "rig") == expected

    def test_the_write_check_ignores_a_read(self):
        source = ("from os import environ\n"
                  "def _restore_gate_modules():\n    environ.get('X')\n    yield\n")
        tree = ast.parse(source)
        assert _environ_writes(tree, _restore_fixture(tree)) == []

    @pytest.mark.parametrize("source", [
        "from os import environ\n"
        "def _restore_gate_modules():\n    environ = {}\n    yield\n",
        "import os\n"
        "def _read():\n    return os.environ.get('X')\n"
        "def _restore_gate_modules():\n    _read()\n    yield\n",
        "import os\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _via():\n    _set()\n"
        "def _restore_gate_modules():\n    _via()\n    yield\n",
        "import os\n"
        "class Other:\n    def _set(self):\n        return 1\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules(other):\n    other._set()\n    yield\n",
        "import os\n"
        "class B:\n    def _set(self):\n        os.environ['X'] = 'warn'\n"
        "class T(B):\n    def _restore_gate_modules(self):\n        self._set()\n        yield\n",
        "import os\n"
        "class B:\n    def _set(self):\n        os.environ['X'] = 'warn'\n"
        "class T(B):\n    def _restore_gate_modules(self):\n        super()._set()\n        yield\n",
        "import os\n"
        "class T:\n    def _set(self):\n        os.environ['X'] = 'warn'\n"
        "    def _restore_gate_modules(self):\n        getattr(self, '_set')()\n        yield\n",
        "import os\nimport sys\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules():\n    sys.modules[__name__]._set()\n    yield\n",
        "import os\nimport tests.rig\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules():\n    tests.rig._set()\n    yield\n",
        "import os\nfrom tests import rig\n"
        "def _set():\n    os.environ['X'] = 'warn'\n"
        "def _restore_gate_modules():\n    rig._set()\n    yield\n",
    ], ids=["rebinding-a-local-name", "helper-that-reads", "helper-two-levels-deep",
            "same-named-method-on-another-object", "inherited-method", "super-call",
            "getattr-method", "sys-modules", "dotted-import-path", "from-import-of-this-file"])
    def test_the_write_check_does_not_count_these(self, source):
        """Rebinding a local name, calling a reading helper and calling another
        object's method write nothing. The rest are the named blind spots,
        pinned as not seen."""
        tree = ast.parse(source)
        assert _environ_writes(tree, _restore_fixture(tree), "rig") == []


class TestEveryTestGetsTheGatesBack:
    """No test hands a test-chosen gate mode to the next one. Two parts make
    that hold, and each has a check that can fail in any test order: the
    restore helper puts the variable and the gate constant back, and this
    module's autouse fixtures that call it are registered for its tests."""

    @pytest.mark.parametrize("module_name,env_name,const_name", [
        ("dispatch_gate", INLINE_ENV, "INLINE_MISSION_MODE"),
        ("handoff_ordering_gate", VARIETY_ENV, "DISPATCH_VARIETY_MODE"),
    ])
    def test_the_helper_restores_each_gate_variable_and_constant(
            self, module_name, env_name, const_name):
        """Inside the helper the gate is reloaded at a mode it does not hold now;
        after the helper the variable and the constant are as they were."""
        module = importlib.import_module(module_name)
        env_before = os.environ.get(env_name)
        const_before = getattr(module, const_name)
        chosen = next(mode for mode in ("shadow", "warn", "deny") if mode != const_before)
        with contextlib.redirect_stderr(io.StringIO()):
            with restored_env_and_reloaded((INLINE_ENV, VARIETY_ENV), _GATE_MODULES):
                os.environ[env_name] = chosen
                importlib.reload(module)
                assert getattr(module, const_name) == chosen, (
                    f"{module_name} did not take {env_name}={chosen}, so the "
                    "restore below would be checked against an unchanged constant")
        assert os.environ.get(env_name) == env_before, (
            f"the helper left {env_name}={os.environ.get(env_name)!r}, "
            f"not {env_before!r}")
        assert getattr(module, const_name) == const_before, (
            f"the helper left {module_name}.{const_name} at "
            f"{getattr(module, const_name)!r}, not {const_before!r}")

    def test_the_restoring_fixtures_are_registered_for_this_module(self, request):
        """Both fixtures that call the helper are active for this module's tests,
        so removing one's autouse wiring fails here, not only a broken helper."""
        missing = {"_reload_gates_after_each_test", "_restore_gate_modules"} - set(
            request.fixturenames)
        assert not missing, f"these restoring fixtures do not run for this module's tests: {missing}"
