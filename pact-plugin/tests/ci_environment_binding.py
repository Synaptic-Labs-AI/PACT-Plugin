"""Run the CI environment check inside the pytest process itself.

Location: pact-plugin/tests/ci_environment_binding.py

Summary: A pytest plugin object that runs scripts/verify_ci_environment.py's
check against THIS interpreter's own sys.path, so the process that runs the
tests is the process that was checked. It checks twice: at session START
(before collection) and at session END, against the session's final
sys.path, so a path a test module or fixture adds during the run is seen
too. Gated: it acts only when GITHUB_ACTIONS is "true" and the file sits in
a PACT source checkout (the .claude-plugin/marketplace.json marker above
it); otherwise it returns at once and loads nothing. Under the gate, drift,
a missing verify script, or a constraints file that is not exact pins ends
the session with exit code 3: at the start through pytest.exit, at the end
by setting the session's exit status (a hook cannot raise pytest.exit
there), even over a run that already failed, which the report then names.

Used by: pact-plugin/conftest.py, which registers CIEnvironmentBinding in
pytest_configure. It is a plugin object rather than a module-level hook
because that conftest already re-exports a pytest_sessionstart.

Stdlib only at import time; pytest and the verify script are loaded only
under the gate.
"""

import importlib.util
import os
import pathlib
import sys

HEADING = "CI ENVIRONMENT DRIFT (pytest process)"
END_HEADING = "CI ENVIRONMENT DRIFT (pytest process, at session end)"


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


def _gated_root():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    return _repo_root()


def _check(root, label):
    """(problem text or None) for this process's environment right now,
    printing the status line. The problem text is the full report."""
    script = root / "scripts" / "verify_ci_environment.py"
    if not script.is_file():
        return f"the verify script is missing: {script}"
    verify = _load_verify_script(script)
    try:
        pins = verify.read_pins(root / "constraints-ci.txt")
    except (verify.ConstraintsError, OSError) as exc:
        return f"CONSTRAINTS ERROR: {exc}"
    verdict = verify.check_environment(pins)
    # The CLI's own first line (sys.executable, which python3, the
    # effective-distribution and closure counts), from the shared
    # interface, so one CI log line names the process the tests ran in.
    print(f"{label}: {verify.measurement(pins)} ok={verdict.ok}")
    return None if verdict.ok else verdict.render()


class CIEnvironmentBinding:
    """Session-start and session-end check of the tested process's
    environment."""

    def pytest_sessionstart(self, session):
        root = _gated_root()
        if root is None:
            return
        import pytest

        problem = _check(root, "ci-environment-binding")
        if problem is not None:
            pytest.exit(f"{HEADING}\n{problem}", returncode=3)

    def pytest_sessionfinish(self, session, exitstatus):
        root = _gated_root()
        if root is None:
            return
        # Leading newline: the progress dots end without one.
        problem = _check(root, "\nci-environment-binding (session end)")
        if problem is None:
            return
        prior = int(exitstatus)
        note = ("" if prior == 0 else
                f"\nThe session had ALREADY ended with exit status {prior} "
                f"before this check; its test results are reported above "
                f"and are not hidden by this exit status.")
        sys.stderr.write(f"\n{END_HEADING}\n{problem}{note}\n")
        sys.stderr.flush()
        session.exitstatus = 3
