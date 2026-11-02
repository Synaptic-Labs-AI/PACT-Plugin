"""Run the CI environment check inside the pytest process itself.

Location: pact-plugin/tests/ci_environment_binding.py

Summary: A pytest plugin object whose session-start hook runs
scripts/verify_ci_environment.py's check against THIS interpreter's own
sys.path, so the process that runs the tests is the process that was
checked. Gated: it acts only when GITHUB_ACTIONS is "true" and the file
sits in a PACT source checkout (the .claude-plugin/marketplace.json
marker above it); otherwise it returns at once and loads nothing.
Under the gate, drift, a missing verify script, or a constraints file
that is not exact pins ends the session with exit code 3.

Used by: pact-plugin/conftest.py, which registers CIEnvironmentBinding in
pytest_configure. It is a plugin object rather than a module-level hook
because that conftest already re-exports a pytest_sessionstart.

Stdlib only at import time; pytest and the verify script are loaded only
under the gate.
"""

import importlib.util
import os
import pathlib

HEADING = "CI ENVIRONMENT DRIFT (pytest process)"


def _repo_root():
    for base in pathlib.Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def _load_verify_script(script):
    spec = importlib.util.spec_from_file_location(
        "_pact_ci_environment_binding_verify", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CIEnvironmentBinding:
    """Session-start check of the tested process's environment."""

    def pytest_sessionstart(self, session):
        if os.environ.get("GITHUB_ACTIONS") != "true":
            return
        root = _repo_root()
        if root is None:
            return
        import pytest

        script = root / "scripts" / "verify_ci_environment.py"
        if not script.is_file():
            pytest.exit(f"{HEADING}\nthe verify script is missing: {script}",
                        returncode=3)
        verify = _load_verify_script(script)
        try:
            pins = verify.read_pins(root / "constraints-ci.txt")
        except (verify.ConstraintsError, OSError) as exc:
            pytest.exit(f"{HEADING}\nCONSTRAINTS ERROR: {exc}", returncode=3)
        verdict = verify.check_environment(pins)
        # The CLI's own first line (sys.executable, which python3, the
        # effective-distribution and closure counts), from the shared
        # interface, so one CI log line names the process the tests ran in.
        print(f"ci-environment-binding: {verify.measurement(pins)} "
              f"ok={verdict.ok}")
        if not verdict.ok:
            pytest.exit(f"{HEADING}\n{verdict.render()}", returncode=3)
