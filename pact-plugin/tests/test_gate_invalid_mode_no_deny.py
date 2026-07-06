"""Coder-flagged item: the get_enum ADDITIVE stderr WARN (emitted when a *_MODE
env var is SET-but-invalid) must never surface as a spurious PreToolUse DENY at
either gate.

Mechanism the tests pin:
- A set-but-invalid mode (e.g. "banana") resolves through get_enum to the
  registry DEFAULT "warn" -- NEVER "deny". Because each gate's deny branch is
  guarded by ``MODE == "deny"``, a "warn" resolution makes the deny branch
  structurally unreachable. (TestInvalidModeResolvesToWarn)
- The additive diagnostic is written to STDERR at gate import, so it cannot
  corrupt the gate's STDOUT decision JSON. (same class -- captures the reload
  stderr)
- Behaviorally, driving the REAL handoff_ordering_gate.main() while the invalid
  mode is active emits valid JSON with NO permissionDecision. (TestInvalidModeMainNeverDenies)

Reload discipline: both gate modules resolve their mode constant at IMPORT, so we
reload under the invalid env and RESTORE them under a clean env at teardown
(without evicting from sys.modules -- sibling test files import + reload them and
would break if they vanished). Mirrors test_pact_config_gate_migration.
"""
import contextlib
import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

INLINE_ENV = "PACT_DISPATCH_INLINE_MISSION_MODE"
VARIETY_ENV = "PACT_DISPATCH_VARIETY_MODE"

# (module, env var, mode-constant name)
_GATES = [
    ("dispatch_gate", INLINE_ENV, "INLINE_MISSION_MODE"),
    ("handoff_ordering_gate", VARIETY_ENV, "DISPATCH_VARIETY_MODE"),
]


@pytest.fixture(scope="module", autouse=True)
def _restore_gate_modules():
    """Reload both gates under a CLEAN env after this file so later tests see
    their default constants. Do NOT evict from sys.modules."""
    yield
    for env in (INLINE_ENV, VARIETY_ENV):
        os.environ.pop(env, None)
    for mod_name in ("dispatch_gate", "handoff_ordering_gate"):
        module = sys.modules.get(mod_name)
        if module is not None:
            importlib.reload(module)


def _reload_capturing_stderr(monkeypatch, module_name, env_name, value, const_name):
    """Set env=value, reload module capturing import-time stderr, return
    (mode_constant, stderr_text)."""
    monkeypatch.setenv(env_name, value)
    module = importlib.import_module(module_name)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        importlib.reload(module)
    return getattr(module, const_name), buf.getvalue()


class TestInvalidModeResolvesToWarn:
    @pytest.mark.parametrize("module_name,env_name,const_name", _GATES)
    def test_invalid_value_resolves_to_warn_not_deny(
        self, monkeypatch, module_name, env_name, const_name
    ):
        mode, _stderr = _reload_capturing_stderr(
            monkeypatch, module_name, env_name, "banana", const_name
        )
        assert mode == "warn", (
            f"{module_name}: an invalid {env_name} must resolve to the safe "
            f"default 'warn', never 'deny' (the deny branch is ==deny-guarded)"
        )

    @pytest.mark.parametrize("module_name,env_name,const_name", _GATES)
    def test_additive_warn_goes_to_stderr_not_stdout(
        self, monkeypatch, module_name, env_name, const_name
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


class TestInvalidModeMainNeverDenies:
    """Behavioral: with the invalid mode active, real main() emits valid JSON and
    NO permissionDecision. (A non-TaskUpdate frame passes through; the point is
    that an invalid mode neither crashes the gate nor flips it to a deny posture,
    and the stderr diagnostic does not corrupt stdout.)"""

    def test_handoff_gate_main_emits_no_deny_under_invalid_mode(self, monkeypatch):
        monkeypatch.setenv(VARIETY_ENV, "banana")
        gate = importlib.import_module("handoff_ordering_gate")
        with contextlib.redirect_stderr(io.StringIO()):
            importlib.reload(gate)  # mode constant now resolved from "banana" -> warn
        benign = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(benign)))
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), pytest.raises(SystemExit) as exc:
            gate.main()
        assert exc.value.code == 0, "invalid mode must not make the gate exit-2 (deny)"
        stdout = out_buf.getvalue().strip()
        parsed = json.loads(stdout)  # must be valid JSON (stderr WARN did not leak in)
        assert "permissionDecision" not in json.dumps(parsed), (
            "invalid mode must never yield a deny; the additive stderr WARN must "
            "not surface as a permissionDecision"
        )
