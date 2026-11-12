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
reload under the invalid env and, at teardown, restore the mode variables to
their values from before this module ran and reload again (without evicting
from sys.modules -- sibling test files import + reload them and would break if
they vanished). Mirrors test_pact_config_gate_migration.
(TestTeardownRestoresAmbientModes pins the restore.)
"""
import contextlib
import importlib
import io
import json
import os
import sys

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


class TestTeardownRestoresAmbientModes:
    """The module teardown helper puts each mode variable back as it found it.

    An operator's shell or a CI job may export a mode variable. Popping it at
    teardown would change the gate constants for every later test on the
    worker, so the helper must restore a set value and keep an absent one
    absent, then reload so the constant matches.
    """

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
        import ast
        from pathlib import Path

        tree = ast.parse((Path(__file__).parent / module_file).read_text(encoding="utf-8"))
        fixture = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_restore_gate_modules")

        def is_environ(node):
            return (isinstance(node, ast.Attribute) and node.attr == "environ"
                    and isinstance(node.value, ast.Name) and node.value.id == "os")

        yields_inside_helper = any(
            isinstance(node, ast.With)
            and any(isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Name)
                    and item.context_expr.func.id == "restored_env_and_reloaded"
                    for item in node.items)
            and any(isinstance(inner, ast.Yield)
                    for statement in node.body for inner in ast.walk(statement))
            for node in ast.walk(fixture))
        assert yields_inside_helper, f"{module_file}: the fixture must yield inside restored_env_and_reloaded"

        writes = [node.lineno for node in ast.walk(fixture) if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("pop", "popitem", "clear", "update", "setdefault")
            and is_environ(node.func.value)
        ) or (
            isinstance(node, (ast.Assign, ast.Delete))
            and any(isinstance(target, ast.Subscript) and is_environ(target.value)
                    for target in getattr(node, "targets", []))
        )]
        assert not writes, f"{module_file}: the fixture writes os.environ at lines {writes}"

    def test_an_absent_value_set_inside_is_removed(self, monkeypatch):
        monkeypatch.delenv(INLINE_ENV, raising=False)
        with contextlib.redirect_stderr(io.StringIO()):
            with restored_env_and_reloaded((INLINE_ENV,), ("dispatch_gate",)):
                os.environ[INLINE_ENV] = "deny"
        assert INLINE_ENV not in os.environ
        assert importlib.import_module("dispatch_gate").INLINE_MISSION_MODE == "warn"
