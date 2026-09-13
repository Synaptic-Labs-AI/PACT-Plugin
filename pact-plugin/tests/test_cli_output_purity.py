"""
Location: pact-plugin/tests/test_cli_output_purity.py

WHAT THIS GUARDS
    The pact-memory CLI (skills/pact-memory/scripts/cli.py) writes a STRUCTURED
    JSON ENVELOPE: to stderr on the error path, to stdout on the success path.
    tests/test_memory_cli.py depends on that contract — it runs the CLI through
    `subprocess.run` and calls `json.loads(result.stderr)` at ELEVEN call sites.

    Nothing else enforces it. Any module on the CLI's import path can break
    every consumer with one well-meaning line to stderr, and a `print` in a
    dependency is not the sort of change anyone reviews for this.

WHY ADVERSE CONDITIONS, NOT THE HAPPY PATH
    An emission that fires on the happy path is caught immediately by the
    existing CLI tests. The dangerous ones fire on the RARE path — a dependency
    is missing, drift is present, a library warns on import — so the contract
    breaks precisely when the error envelope is the thing being read. A purity
    test that only exercised the happy path would reproduce that same blind
    spot one level up.

    So every arm below runs the CLI with the pact-memory dependency set made
    UNIMPORTABLE and with `CI` set, which is the combination that drives
    memory_init into its drift branch.

HOW THIS MODULE IS KEPT HONEST
    A guard that cannot fire is worse than no guard, so three self-checks sit
    beside the assertions:
      - the blocker is shown to actually raise ImportError (the adverse
        condition is real, not an inert fixture);
      - the same import is shown to SUCCEED without the blocker (the blocker is
        the cause, not a bare environment);
      - an import-time emission is injected and the parse is required to FAIL
        (this module can detect the thing it exists to detect).

RELATED
    skills/pact-memory/scripts/cli.py         emits the envelopes
    skills/pact-memory/scripts/memory_init.py the drift branch, deliberately
                                              silent for exactly this reason
    tests/test_memory_cli.py                  the consumers this protects
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "pact-memory" / "scripts" / "cli.py"
)

# The import names memory_init probes for the pact-memory dependency set.
# Blocking all of them is what pushes check_and_install_dependencies past its
# "nothing missing" early return and into the branch under test.
BLOCKED_IMPORTS = ("pysqlite3", "sqlite_vec", "model2vec")

# Injected via sitecustomize.py, which CPython imports automatically at
# interpreter start-up. That places the hook genuinely ON THE IMPORT PATH,
# before the CLI runs, which is where a real offending library would sit.
_BLOCKER_SOURCE = '''\
import os
import sys

if os.environ.get("PACT_TEST_CLOCK_SHIFT_SECONDS"):
    import clock_shift_shim  # noqa: F401  (chains the clock-shift sitecustomize this file shadows)

_BLOCKED = {blocked!r}


class _Blocker:
    """Raise ImportError for the pact-memory dependency set."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in _BLOCKED:
            raise ImportError("blocked by test fixture: " + fullname)
        return None


sys.meta_path.insert(0, _Blocker())
'''

# The mutation: a well-meaning library announcing itself on import.
_EMISSION_SOURCE = '''
print("pact-memory: dependency drift detected", file=sys.stderr)
'''

_EMISSION_MARKER = "dependency drift detected"


def _make_hook_dir(tmp_path, name, emit_to_stderr=False):
    """
    Write a sitecustomize.py that blocks the dependency set.

    Args:
        tmp_path: pytest tmp_path fixture.
        name: subdirectory name, so several hooks can coexist in one test.
        emit_to_stderr: also write a line to stderr on import (the mutation).

    Returns:
        Path to the directory to place on PYTHONPATH.
    """
    hook_dir = tmp_path / name
    hook_dir.mkdir()
    source = _BLOCKER_SOURCE.format(blocked=set(BLOCKED_IMPORTS))
    if emit_to_stderr:
        source += _EMISSION_SOURCE
    (hook_dir / "sitecustomize.py").write_text(source)
    return hook_dir


def _env_with_hook(hook_dir, extra=None):
    """Build a subprocess env with the hook prepended to PYTHONPATH."""
    env = dict(os.environ)
    if hook_dir is not None:
        existing = env.get("PYTHONPATH", "")
        parts = [str(hook_dir)] + ([existing] if existing else [])
        env["PYTHONPATH"] = os.pathsep.join(parts)
    # Drives memory_init to the drift branch rather than a mid-run install.
    env["CI"] = "true"
    if extra:
        env.update(extra)
    return env


def _run_cli(args, hook_dir, extra_env=None):
    """Run cli.py as a real subprocess, the way the CLI tests do."""
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        capture_output=True, text=True, timeout=60,
        env=_env_with_hook(hook_dir, extra_env),
    )


def _parse_or_fail(stream_text, label):
    """Parse a CLI stream as JSON, failing with the diagnosis rather than a traceback."""
    try:
        return json.loads(stream_text)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"CLI {label} did not parse as JSON, so every consumer that calls "
            f"json.loads(result.{label}) is broken — including the eleven call "
            f"sites in tests/test_memory_cli.py. Something on the CLI's import "
            f"path wrote to {label}.\n"
            f"--- raw {label} ---\n{stream_text!r}\n"
            f"--- decode error ---\n{exc}"
        )


def _dependency_importable():
    """True when the blocked-import control has something real to block."""
    try:
        __import__(BLOCKED_IMPORTS[0])
        return True
    except ImportError:
        return False


class TestTheAdverseConditionIsReal:
    """Controls. Without these, every assertion below could pass vacuously."""

    def test_the_blocker_actually_blocks(self, tmp_path):
        """The fixture raises ImportError rather than silently doing nothing."""
        hook_dir = _make_hook_dir(tmp_path, "block")
        proc = subprocess.run(
            [sys.executable, "-c", f"import {BLOCKED_IMPORTS[0]}"],
            capture_output=True, text=True, timeout=60,
            env=_env_with_hook(hook_dir),
        )
        assert proc.returncode != 0, (
            "the dependency imported despite the blocker, so every 'missing "
            "dependency' arm in this module is testing the ordinary path"
        )
        assert "blocked by test fixture" in proc.stderr

    @pytest.mark.skipif(
        not _dependency_importable(),
        reason=(
            "converse control needs the dependency actually installed; it is "
            "absent here, so a failed import would prove nothing about the hook"
        ),
    )
    def test_the_blocker_is_the_cause_not_a_bare_environment(self):
        """The same import SUCCEEDS without the hook, so the hook is the cause."""
        proc = subprocess.run(
            [sys.executable, "-c", f"import {BLOCKED_IMPORTS[0]}"],
            capture_output=True, text=True, timeout=60,
            env=_env_with_hook(None),
        )
        assert proc.returncode == 0, (
            "import failed with no blocker installed, so the blocked arm above "
            f"cannot be attributed to the fixture: {proc.stderr}"
        )


class TestCliOutputPurity:
    """The contract: the CLI's streams stay machine-readable under stress."""

    def test_stderr_is_pure_json_when_dependencies_are_missing(
        self, tmp_path, memory_store
    ):
        hook_dir = _make_hook_dir(tmp_path, "block")
        result = _run_cli(
            ["get", "nonexistent99", "--db-path", str(memory_store("m.db"))],
            hook_dir,
        )

        assert result.returncode == 1
        envelope = _parse_or_fail(result.stderr, "stderr")
        assert envelope["ok"] is False
        assert envelope["error"] == "NOT_FOUND"

    def test_stderr_is_pure_json_under_forced_warnings(self, tmp_path, memory_store):
        """
        PYTHONWARNINGS=always defeats the default warning filters, so any
        `warnings.warn` reachable on this path is forced onto stderr. This is
        the arm that would catch a warning-based signal being added later.
        """
        hook_dir = _make_hook_dir(tmp_path, "block")
        result = _run_cli(
            ["get", "nonexistent99", "--db-path", str(memory_store("m.db"))],
            hook_dir,
            extra_env={"PYTHONWARNINGS": "always"},
        )

        assert result.returncode == 1
        envelope = _parse_or_fail(result.stderr, "stderr")
        assert envelope["error"] == "NOT_FOUND"

    def test_success_path_keeps_stdout_json_and_stderr_silent(
        self, tmp_path, memory_store
    ):
        """
        The success path carries the same exposure: stdout is the envelope, and
        stderr must stay EMPTY so a caller can treat any stderr content as a
        real diagnostic rather than noise.
        """
        hook_dir = _make_hook_dir(tmp_path, "block")
        result = _run_cli(
            ["list", "--db-path", str(memory_store("m.db"))],
            hook_dir,
        )

        assert result.returncode == 0
        payload = _parse_or_fail(result.stdout, "stdout")
        assert payload["ok"] is True
        assert result.stderr == "", (
            "the CLI wrote to stderr on a SUCCESSFUL run, so stderr no longer "
            f"distinguishes failure from noise: {result.stderr!r}"
        )


class TestThisGuardCanFire:
    """
    Mutation arm. Injects the exact defect the module exists to catch and
    requires the contract to break — otherwise the assertions above would pass
    against a CLI that could not be broken, and would prove nothing.
    """

    def test_an_import_time_stderr_write_breaks_the_parse(self, tmp_path, memory_store):
        hook_dir = _make_hook_dir(tmp_path, "emit", emit_to_stderr=True)
        result = _run_cli(
            ["get", "nonexistent99", "--db-path", str(memory_store("m.db"))],
            hook_dir,
        )

        assert _EMISSION_MARKER in result.stderr, (
            "the injected emission never reached stderr, so this arm did not "
            "exercise the mutation it claims to"
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stderr)

    def test_the_mutation_differs_from_the_guarded_case_only_by_the_emission(
        self, tmp_path, memory_store
    ):
        """
        Both arms block the same imports and run the same command; the ONLY
        difference is the added stderr write. That is what licenses attributing
        the broken parse to the emission rather than to the adverse conditions.
        """
        args = ["get", "nonexistent99", "--db-path", str(memory_store("m.db"))]
        clean = _run_cli(args, _make_hook_dir(tmp_path, "clean"))
        mutated = _run_cli(args, _make_hook_dir(tmp_path, "dirty", emit_to_stderr=True))

        assert clean.returncode == mutated.returncode
        assert json.loads(clean.stderr)["error"] == "NOT_FOUND"
        assert mutated.stderr.endswith(clean.stderr), (
            "the mutated run should be the clean envelope with the emission "
            "prepended; it differs in some other way, so the comparison does "
            "not isolate the emission"
        )
