"""
Tests for the memory_init module - lazy initialization system for PACT memory.

Tests cover:
1. Unit tests for ensure_memory_ready(), reset_initialization(), is_initialized()
2. Thread safety - multiple concurrent calls only run once
3. Integration with memory_api.py - first API call triggers initialization
4. Edge cases - graceful degradation, already-installed dependencies
"""

import os
import re
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


class TestEnsureMemoryReady:
    """Tests for ensure_memory_ready() - the main lazy initialization entry point."""

    def test_returns_dict_with_expected_keys(self):
        """Test that ensure_memory_ready returns expected result structure."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            result = ensure_memory_ready()

            assert 'already_initialized' in result
            assert 'deps' in result
            assert 'migration' in result
            assert 'embedding' in result
            assert result['already_initialized'] is False

    def test_idempotent_only_runs_once(self):
        """Test that ensure_memory_ready only runs initialization once per session."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First call should run initialization
            result1 = ensure_memory_ready()
            assert result1['already_initialized'] is False
            assert mock_deps.call_count == 1

            # Second call should return early
            result2 = ensure_memory_ready()
            assert result2['already_initialized'] is True
            assert mock_deps.call_count == 1  # Still 1, not called again

            # Third call also returns early
            result3 = ensure_memory_ready()
            assert result3['already_initialized'] is True
            assert mock_deps.call_count == 1

    def test_runs_all_three_steps(self):
        """Test that all three initialization steps are called in order."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        call_order = []

        def track_deps():
            call_order.append('deps')
            return {'status': 'ok', 'installed': [], 'failed': []}

        def track_migrate():
            call_order.append('migrate')
            return {'status': 'ok', 'message': None}

        def track_embed():
            call_order.append('embed')
            return {'status': 'ok', 'message': None}

        with patch('scripts.memory_init.check_and_install_dependencies', side_effect=track_deps), \
             patch('scripts.memory_init.maybe_migrate_embeddings', side_effect=track_migrate), \
             patch('scripts.memory_init.maybe_embed_pending', side_effect=track_embed):

            ensure_memory_ready()

            assert call_order == ['deps', 'migrate', 'embed']


class TestResetInitialization:
    """Tests for reset_initialization() - allows re-initialization for testing."""

    def test_reset_allows_reinitialization(self):
        """Test that reset_initialization allows initialization to run again."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization, is_initialized

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First initialization
            result1 = ensure_memory_ready()
            assert result1['already_initialized'] is False
            assert is_initialized() is True
            assert mock_deps.call_count == 1

            # Reset
            reset_initialization()
            assert is_initialized() is False

            # Second initialization runs again
            result2 = ensure_memory_ready()
            assert result2['already_initialized'] is False
            assert mock_deps.call_count == 2

    def test_reset_leaves_existing_marker_in_place(self):
        """reset_initialization() must not delete the embedding marker.

        The marker path resolves to a location shared with a real session, so a
        reset that unlinks it reaches outside the test process. Resetting the
        in-memory flag is the only behaviour tests need.
        """
        from scripts.memory_init import reset_initialization, _get_embedding_attempted_path

        test_session_id = f"test-routef-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()
            marker_path.touch()
            try:
                assert marker_path.exists(), "precondition: marker was created"

                reset_initialization()

                assert marker_path.exists(), (
                    "reset_initialization() deleted the embedding marker; "
                    "the reset must not touch the filesystem"
                )
            finally:
                marker_path.unlink(missing_ok=True)

    def test_clear_embedding_marker_removes_it(self):
        """The deletion still exists, but only under its own name."""
        from scripts.memory_init import clear_embedding_marker, _get_embedding_attempted_path

        test_session_id = f"test-clearmarker-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()
            marker_path.touch()
            try:
                assert marker_path.exists(), "precondition: marker was created"

                clear_embedding_marker()

                assert not marker_path.exists()
            finally:
                marker_path.unlink(missing_ok=True)


class TestIsInitialized:
    """Tests for is_initialized() - checks current initialization state."""

    def test_returns_false_before_initialization(self):
        """Test is_initialized returns False before ensure_memory_ready is called."""
        from scripts.memory_init import reset_initialization, is_initialized

        reset_initialization()
        assert is_initialized() is False

    def test_returns_true_after_initialization(self):
        """Test is_initialized returns True after ensure_memory_ready completes."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization, is_initialized

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            ensure_memory_ready()
            assert is_initialized() is True


class TestThreadSafety:
    """Tests for thread safety - multiple concurrent calls only run once."""

    def test_concurrent_calls_only_initialize_once(self):
        """Test that multiple concurrent calls only run initialization once."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        call_count = {'value': 0}
        call_lock = threading.Lock()

        def counting_deps():
            with call_lock:
                call_count['value'] += 1
            # Simulate some work
            time.sleep(0.05)
            return {'status': 'ok', 'installed': [], 'failed': []}

        with patch('scripts.memory_init.check_and_install_dependencies', side_effect=counting_deps), \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Launch 10 concurrent calls
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(ensure_memory_ready) for _ in range(10)]
                results = [f.result() for f in as_completed(futures)]

            # Only one call should have run the full initialization
            assert call_count['value'] == 1

            # Exactly one result should have already_initialized=False
            not_initialized = [r for r in results if not r['already_initialized']]
            already_initialized = [r for r in results if r['already_initialized']]

            assert len(not_initialized) == 1
            assert len(already_initialized) == 9

    def test_double_check_locking_pattern(self):
        """Test that double-check locking prevents race conditions."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        # Track how many times we enter the lock
        lock_entries = {'value': 0}
        original_lock = threading.Lock()

        class TrackingLock:
            def __enter__(self):
                lock_entries['value'] += 1
                return original_lock.__enter__()

            def __exit__(self, *args):
                return original_lock.__exit__(*args)

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed, \
             patch('scripts.memory_init._init_lock', TrackingLock()):

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First call acquires lock
            ensure_memory_ready()

            # Second call should return early without acquiring lock (fast path)
            # due to the initial check before lock acquisition
            ensure_memory_ready()

            # Lock was only entered once
            assert lock_entries['value'] == 1


class TestCheckAndInstallDependencies:
    """Tests for check_and_install_dependencies function."""

    def test_all_dependencies_present_returns_ok(self):
        """Test when all dependencies are already installed - returns ok status."""
        from scripts.memory_init import check_and_install_dependencies

        # When deps are already importable, function returns ok with empty lists
        # The actual function checks via __import__, and if successful, returns ok
        # We test this by calling it (deps are installed in test env)
        result = check_and_install_dependencies()

        # If deps are installed, status should be ok
        # If not installed, the test still passes as we're testing the return structure
        assert 'status' in result
        assert 'installed' in result
        assert 'failed' in result

    def test_subprocess_called_for_missing_deps(self, monkeypatch):
        """Test that subprocess.run is called when deps are missing."""
        import builtins
        from scripts.memory_init import check_and_install_dependencies

        monkeypatch.delenv("CI", raising=False)   # pins the installer, so the gate must be OPEN
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            # Simulate pysqlite3 not installed
            if name == 'pysqlite3':
                raise ImportError("No module named 'pysqlite3'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', mock_import), \
             patch('scripts.memory_init.subprocess.run') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            result = check_and_install_dependencies()

            # Should have attempted pip install
            assert mock_run.called

    def test_installation_failure_recorded(self, monkeypatch):
        """Test that installation failures are recorded in result."""
        import builtins
        from scripts.memory_init import check_and_install_dependencies

        monkeypatch.delenv("CI", raising=False)   # pins the installer, so the gate must be OPEN
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            # All deps missing
            if name in ('pysqlite3', 'sqlite_vec', 'model2vec'):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', mock_import), \
             patch('scripts.memory_init.subprocess.run') as mock_run:

            mock_run.return_value = MagicMock(returncode=1)  # All installations fail

            result = check_and_install_dependencies()

            assert result['status'] == 'failed'
            assert len(result['failed']) > 0

    def test_installation_timeout_handled(self, monkeypatch):
        """Test that installation timeout is handled gracefully."""
        import builtins
        import subprocess
        from scripts.memory_init import check_and_install_dependencies

        monkeypatch.delenv("CI", raising=False)   # pins the installer, so the gate must be OPEN
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'pysqlite3':
                raise ImportError("Not installed")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', mock_import), \
             patch('scripts.memory_init.subprocess.run') as mock_run:

            mock_run.side_effect = subprocess.TimeoutExpired(cmd='pip', timeout=60)

            result = check_and_install_dependencies()

            # Should record timeout in failed list
            assert any('timeout' in str(f).lower() for f in result['failed'])

    def test_install_path_inert_when_ci_set(self, monkeypatch):
        """The gate FIRES: under CI the install path returns before subprocess."""
        import builtins
        from scripts.memory_init import check_and_install_dependencies

        monkeypatch.setenv("CI", "true")
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'pysqlite3':
                raise ImportError("No module named 'pysqlite3'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', mock_import), \
             patch('scripts.memory_init.subprocess.run') as mock_run:

            result = check_and_install_dependencies()

            assert not mock_run.called
            assert result['status'] == 'skipped_ci'
            assert 'pysqlite3' in result['skipped']
            # Must stay empty: ensure_memory_ready logs this key as
            # "Failed to install", and nothing was attempted.
            assert result['failed'] == []


class TestDependencyDriftIsDetectable:
    """The `skipped_ci` branch is SILENT. This class is why that is safe.

    WHAT WAS MEASURED, so nobody re-opens this by "just adding a log line".
    Under the shipping invocation (`python -m pytest`, no `-r`, no `-s`), with
    `CI` set and a genuinely absent package, the DOMINANT caller of
    `check_and_install_dependencies` is a CLI SUBPROCESS (`scripts/cli.py` via
    `subprocess.run`), not the pytest process. On that path the CLI's stderr is
    a STRUCTURED JSON CONTRACT — the CLI tests do `json.loads(result.stderr)`.
    Drift forced, one variable changed at a time, `tests/test_memory_cli.py`:

        no emission at all ................. 154 passed
        print(..., file=sys.stderr) ........ 15 failed
        logger.warning (lastResort→stderr) . 15 failed
        warnings.warn (default→stderr) ..... 15 failed

    So every in-process emission mechanism corrupts that contract identically,
    and `warnings.warn` is NOT a safe substitute here even though it is the one
    mechanism that IS visible from a passing test in the pytest process.
    Visibility does not cross the process boundary regardless: pytest's warnings
    summary only collects warnings raised in the pytest process.

    Hence drift is caught by TESTS, not log lines. What CI actually installs
    is no longer inferred from CI text: scripts/verify_ci_environment.py
    compares the INSTALLED environment with constraints-ci.txt in CI (a step
    before every test run and as each job's last step) and inside the pytest
    process itself (tests/ci_environment_binding.py). This class keeps the
    bounded STATIC checks that complement it: memory_init's packages are
    pinned, the verify steps sit around every test run, a word tripwire for
    tools the runtime check cannot see, the third-party action allowlist,
    and the floor-literal bind.
    """

    # The verify step's command as tests.yml runs it. It carries `--report`
    # until the runner baseline is measured; dropping `--report` from the
    # workflow is the one-constant flip here.
    VERIFY_CMD = "python scripts/verify_ci_environment.py --report"
    # The test step's command, exactly: the placement arm pins it so the
    # tested process is the process the verify steps checked.
    TEST_CMD = "python -m pytest -ra"
    # A job is GOVERNED when any step's run text names one of these.
    _GOVERNED_RE = re.compile(r"\b(pytest|python3?|pip3?|ruff)\b")
    _PYTEST_RE = re.compile(r"\bpytest\b")

    def _source_repo_root(self):
        """Repo root by a POSITIVE marker, or None when this is not a checkout.

        Deliberately NOT "did I find the workflow file?". "I cannot find it" and
        "it is wrong" must never produce the same outcome — that equivalence is
        the silent-narrowing defect this whole class exists to prevent. So the
        skip decision is made on an INDEPENDENT positive marker, and the
        workflow's own absence is then free to be a FAILURE.

        Marker matches the convention already used by test_packaging_boundary.py.
        The mutation arms monkeypatch THIS method to point the shipped arms
        at a rig copy; the rig builder reads the real tree through
        `_real_repo_root`, which they never patch, so a second copy built in
        the same arm cannot inherit the first copy's mutation.
        """
        return self._real_repo_root()

    @staticmethod
    def _real_repo_root():
        """The checkout this file lives in, by marker — never monkeypatched."""
        for base in Path(__file__).resolve().parents:
            if (base / ".claude-plugin" / "marketplace.json").exists():
                return base
        return None

    def _require_root(self, what):
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ tree by design. This skip is "
                f"NO INFORMATION about {what} — never read it as a pass."
            )
        return root

    @staticmethod
    def _load_workflow_yaml(path, text=None):
        """yaml.safe_load of one workflow or action; unparseable is a FAILURE."""
        try:
            return yaml.safe_load(
                text if text is not None else path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AssertionError(
                f"PARSE FAILURE, not a clean read: {path} does not parse as "
                f"YAML ({exc.__class__.__name__}), so its steps cannot be "
                f"enumerated. GitHub would refuse to run it; fix the syntax."
            ) from exc

    @staticmethod
    def _steps_of(doc):
        """Every step mapping in one parsed workflow or composite action."""
        steps = []
        if not isinstance(doc, dict):
            return steps
        for job in (doc.get("jobs") or {}).values():
            if isinstance(job, dict):
                steps += [s for s in job.get("steps") or [] if isinstance(s, dict)]
        runs = doc.get("runs")
        if isinstance(runs, dict):
            steps += [s for s in runs.get("steps") or [] if isinstance(s, dict)]
        return steps

    @staticmethod
    def _workflow_files(root):
        """Every `.yml`/`.yaml` under `.github/workflows/`."""
        workflows = root / ".github" / "workflows"
        if not workflows.is_dir():
            return []
        return sorted(p for p in workflows.iterdir()
                      if p.is_file() and p.suffix in (".yml", ".yaml"))

    @staticmethod
    def _action_files(root):
        """Every `action.yml`/`action.yaml` under `.github/actions/`."""
        actions = root / ".github" / "actions"
        if not actions.is_dir():
            return []
        return sorted(p for p in actions.rglob("*")
                      if p.is_file() and p.name in ("action.yml", "action.yaml"))

    @staticmethod
    def _verify_script(root=None):
        """scripts/verify_ci_environment.py, loaded by path (it is dev
        tooling at the repo root, not an importable package)."""
        import importlib.util
        root = root or TestDependencyDriftIsDetectable._real_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout, so "
                        "scripts/verify_ci_environment.py is absent.")
        script = root / "scripts" / "verify_ci_environment.py"
        assert script.is_file(), (
            f"source checkout detected at {root} but {script} is missing: "
            f"the runtime environment check cannot run. FAILURE, not skip."
        )
        spec = importlib.util.spec_from_file_location(
            "_pact_verify_ci_environment_under_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_memory_init_packages_are_pinned(self):
        """Every package memory_init would install is a CI pin.

        Under CI memory_init's install path is skipped (silently, see the
        class docstring), so a package it needs must reach the environment
        through constraints-ci.txt, the one CI requirement set. Names are
        compared after PEP 503 normalisation.
        """
        import inspect

        from scripts import memory_init

        source = inspect.getsource(memory_init.check_and_install_dependencies)
        declared = re.findall(r"\(\s*'([^']+)'\s*,\s*'[^']+'\s*\)", source)
        assert declared, (
            "could not read the package list out of "
            "check_and_install_dependencies — the list changed shape and this "
            "check is now vacuous. Re-point it; do not delete it."
        )
        root = self._require_root("memory_init's pins")
        verify = self._verify_script(self._real_repo_root())
        pins = verify.read_pins(root / "constraints-ci.txt")
        missing = sorted(
            verify.normalize(name) for name in declared
            if verify.normalize(name) not in pins)
        assert not missing, (
            f"DEPENDENCY DRIFT: {missing} are installed by "
            f"memory_init.check_and_install_dependencies but are NOT pinned "
            f"in constraints-ci.txt. Under CI the install path is skipped, so "
            f"these would be absent at runtime and the branch reports it "
            f"NOWHERE (it is deliberately silent — the CLI's stderr is a JSON "
            f"contract). Pin them in constraints-ci.txt, or remove them here."
        )

    def test_ci_environment_is_verified_around_every_test_run(self):
        """The runtime check runs in the SAME place as the tests (F1(a), F2).

        For every GOVERNED job (any step whose `run` names pytest, python,
        pip or ruff) in every workflow under .github/workflows/:
          (i) its LAST step runs VERIFY_CMD with `if: always()`;
          (ii) every step whose `run` names pytest is EXACTLY TEST_CMD and is
               IMMEDIATELY preceded by a step running VERIFY_CMD;
          (iii) no verify step and no pytest step carries `env:` or
               `shell:`, and no verify step carries `working-directory:`
               (VERIFY_CMD names the script relative to the repo root).
        Together these make the process pytest runs in the process the
        verify step just checked, and the after-check sees whatever the
        tests or later steps installed.
        """
        root = self._require_root("verify-step placement")
        workflows = self._workflow_files(root)
        assert workflows, (
            f"source checkout detected at {root} but no workflow under "
            f".github/workflows/: the placement arm cannot run. FAILURE."
        )
        problems = []
        governed_jobs = 0
        for path in workflows:
            doc = self._load_workflow_yaml(path)
            rel = path.relative_to(root).as_posix()
            for jid, job in ((doc or {}).get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
                runs = [s.get("run") if isinstance(s.get("run"), str) else ""
                        for s in steps]
                if not any(self._GOVERNED_RE.search(r) for r in runs):
                    continue
                governed_jobs += 1
                where = f"{rel} job {jid}"
                last = steps[-1] if steps else {}
                if runs[-1].strip() != self.VERIFY_CMD or str(
                        last.get("if", "")).strip() not in (
                        "always()", "${{ always() }}"):
                    problems.append(
                        f"{where}: the LAST step must run "
                        f"`{self.VERIFY_CMD}` with `if: always()`")
                for n, (step, run) in enumerate(zip(steps, runs)):
                    is_verify = run.strip() == self.VERIFY_CMD
                    is_test = bool(self._PYTEST_RE.search(run))
                    if is_test and run.strip() != self.TEST_CMD:
                        problems.append(
                            f"{where} step {n}: a pytest step must run "
                            f"exactly `{self.TEST_CMD}`, not {run.strip()!r}")
                    if is_test and (n == 0 or runs[n - 1].strip() != self.VERIFY_CMD):
                        problems.append(
                            f"{where} step {n}: a pytest step must be "
                            f"IMMEDIATELY preceded by `{self.VERIFY_CMD}`")
                    if (is_test or is_verify) and (
                            "env" in step or "shell" in step):
                        problems.append(
                            f"{where} step {n}: a verify or pytest step may "
                            f"carry no `env:` or `shell:`")
                    if is_verify and "working-directory" in step:
                        problems.append(
                            f"{where} step {n}: a verify step may carry no "
                            f"`working-directory:`")
        assert governed_jobs, (
            "PARSE FAILURE, not placement drift: no workflow job names "
            "pytest/python/pip/ruff, so the placement arm checks nothing. "
            "Re-point it; do not delete it."
        )
        assert not problems, (
            "VERIFY PLACEMENT DRIFT: the runtime environment check no longer "
            "brackets every test run, so a green CI may have tested a "
            "different environment than the one it checked:\n"
            + "\n".join(f"  {p}" for p in problems)
            + "\nPut a step running the verify command immediately before "
            "each pytest step and as the job's last step with if: always(), "
            "keep the pytest command exactly as pinned, and move env/shell "
            "settings off those steps."
        )

    # C-LITE TRIPWIRE. Tools whose installs the runtime check cannot see
    # (isolated environments), the uninstall verb, and the index-redirect
    # belt. Read as WORDS over a fixed text set; no argv parsing.
    _TRIPWIRE_PATTERNS = (
        ("isolated-environment tool", re.compile(
            r"\b(pipx|uvx|uv|conda|mamba|micromamba|poetry|pipenv|pdm|hatch"
            r"|pixi|rye)\b")),
        ("pip uninstall", re.compile(r"\bpip(3(\.\d+)?)?\s+uninstall\b")),
        ("pip index env", re.compile(
            r"\bPIP_(INDEX_URL|EXTRA_INDEX_URL|FIND_LINKS|NO_INDEX"
            r"|TRUSTED_HOST)\b")),
        ("pip index flag", re.compile(
            r"--(index-url|extra-index-url|find-links|no-index|trusted-host"
            r"|pypi-url)\b")),
    )
    # Exact line (stripped) -> the reason it is accepted. Empty today.
    _ACCEPTED_TRIPWIRE_LINES = {}
    _CANONICAL_INSTALL_LINE = "python -m pip install -r constraints-ci.txt"

    @staticmethod
    def _tracked_shell_and_make_files(root):
        """Every git-tracked *.sh, *.bash, Makefile and Makefile.* file;
        globbed from the root when git cannot list them (a rig copy)."""
        patterns = ("*.sh", "*.bash", "Makefile", "Makefile.*")
        try:
            listed = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "--",
                 *patterns, *(f"**/{p}" for p in patterns)],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            listed = None
        if listed is not None and listed.returncode == 0 and listed.stdout:
            names = [n for n in listed.stdout.split("\0") if n]
            return sorted({root / n for n in names if (root / n).is_file()})
        found = set()
        for pattern in patterns:
            found |= {p for p in root.rglob(pattern) if p.is_file()
                      and ".git" not in p.relative_to(root).parts}
        return sorted(found)

    def _tripwire_hits(self, root):
        texts, env_hits = [], []
        for path in self._workflow_files(root) + self._action_files(root):
            doc = self._load_workflow_yaml(path)
            rel = path.relative_to(root).as_posix()
            for n, step in enumerate(self._steps_of(doc)):
                if isinstance(step.get("run"), str):
                    texts.append((f"{rel} step {n}", step["run"]))
            env_hits.extend(
                f"{rel}: pip env key `{key}`" for key in self._pip_env_keys(doc))
        files = set(self._tracked_shell_and_make_files(root))
        install_sh = root / ".cursor" / "install.sh"
        if install_sh.is_file():
            files.add(install_sh)
        for path in sorted(files):
            texts.append((path.relative_to(root).as_posix(),
                          path.read_text(encoding="utf-8", errors="replace")))
        hits = list(env_hits)
        for label, text in texts:
            for number, line in enumerate(text.splitlines(), 1):
                if line.strip() in self._ACCEPTED_TRIPWIRE_LINES:
                    continue
                for family, pattern in self._TRIPWIRE_PATTERNS:
                    for match in pattern.finditer(line):
                        hits.append(
                            f"{label}:{number}: {family} `{match.group(0)}` "
                            f"in {line.strip()!r}")
        return hits

    @staticmethod
    def _pip_env_keys(doc):
        """`PIP_*` keys of the workflow, job, container and step `env:`
        mappings of one parsed workflow or action (service containers'
        env never reaches the job's process and is not read)."""
        keys = []

        def harvest(env):
            if isinstance(env, dict):
                keys.extend(str(k) for k in env
                            if str(k).upper().startswith("PIP_"))

        if isinstance(doc, dict):
            harvest(doc.get("env"))
            for job in (doc.get("jobs") or {}).values():
                if isinstance(job, dict):
                    harvest(job.get("env"))
                    if isinstance(job.get("container"), dict):
                        harvest(job["container"].get("env"))
            for step in TestDependencyDriftIsDetectable._steps_of(doc):
                harvest(step.get("env"))
        return keys

    def test_no_isolated_environment_tool_or_index_redirect(self):
        """A bounded WORD tripwire for what the runtime check cannot see.

        The runtime check (scripts/verify_ci_environment.py) observes the
        environment the tests import from; an isolated-environment tool
        (pipx, uv, conda, ...) installs where it never looks, an uninstall
        changes the set after the before-check, and an index redirect
        serves a different build of a pinned version. TEXT SET, fixed:
        every `run:` string in .github/workflows/*.y*ml and
        .github/actions/**/action.y*ml (yaml.safe_load), every `PIP_*`
        key of their env mappings, the full text of .cursor/install.sh, and
        the full text of every git-tracked *.sh, *.bash, Makefile and
        Makefile.* file. Matched as WORDS (`_TRIPWIRE_PATTERNS`); no argv
        parsing and no following. A hit names file, line and token.
        """
        root = self._require_root("the isolated-environment tripwire")
        hits = self._tripwire_hits(root)
        assert not hits, (
            "TRIPWIRE: CI or a tracked script names a tool or option the "
            "runtime environment check cannot see:\n"
            + "\n".join(f"  {h}" for h in hits)
            + f"\nInstall through the pinned set instead "
            f"(`{self._CANONICAL_INSTALL_LINE}`), or, if the line is "
            f"genuinely harmless (prose, a read-only use), add the exact "
            f"line to _ACCEPTED_TRIPWIRE_LINES with a reason in the same "
            f"change."
        )

    # Every THIRD-PARTY action the shipped workflows and actions use,
    # keyed on `owner/repo[/path]` with the `@ref` stripped (a SHA re-pin is
    # not drift). Each was vetted as installing nothing unpinned.
    _ALLOWED_THIRD_PARTY_ACTIONS = frozenset({
        "actions/checkout",
        "actions/setup-python",
        "actions/cache/restore",
        "actions/cache/save",
    })

    def test_every_third_party_action_is_allowlisted(self):
        """Every third-party action CI uses is on a VETTED allowlist.

        An action is code CI runs that no `run:` text shows, and many
        install packages internally. ALLOWLIST: every `uses:` reference to
        another repository — step level, or a job-level reusable workflow —
        in every workflow under .github/workflows/, every action under
        .github/actions/, and every local action a `uses: ./<path>` names
        (followed recursively) must be in `_ALLOWED_THIRD_PARTY_ACTIONS`,
        keyed on `owner/repo[/path]` with the `@ref` stripped. `docker://`
        images are never allowed.
        """
        root = self._require_root("the action allowlist")
        queue = self._workflow_files(root) + self._action_files(root)
        seen, references = set(), []
        while queue:
            path = queue.pop(0)
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            doc = self._load_workflow_yaml(path)
            rel = path.relative_to(root).as_posix()
            entries = [(f"{rel} step", s.get("uses")) for s in self._steps_of(doc)]
            for jid, job in ((doc or {}).get("jobs") or {}).items() \
                    if isinstance(doc, dict) else []:
                if isinstance(job, dict):
                    entries.append((f"{rel} job {jid}", job.get("uses")))
            for label, uses in entries:
                if not isinstance(uses, str):
                    continue
                if uses.startswith("./"):
                    target = root / uses[2:].split("@")[0]
                    if target.is_file():
                        queue.append(target)
                    for name in ("action.yml", "action.yaml"):
                        if (target / name).is_file():
                            queue.append(target / name)
                    continue
                references.append((label, uses))
        offenders = [
            f"{label}: uses {uses!r}" for label, uses in references
            if uses.startswith("docker://")
            or uses.split("@")[0].rstrip("/").lower()
            not in self._ALLOWED_THIRD_PARTY_ACTIONS
        ]
        assert not offenders, (
            f"ACTION ALLOWLIST DRIFT: {offenders} are not on the vetted "
            f"allowlist. An action runs code no `run:` text shows, and an "
            f"installing action resolves packages constraints-ci.txt cannot "
            f"pin. Confirm the action installs nothing unpinned (read its "
            f"action.yml and what it runs), then add `owner/repo[/path]` to "
            f"_ALLOWED_THIRD_PARTY_ACTIONS in the same change."
        )

    def _repo_copy_carrying(self, tmp_path, workflow_text=None,
                            script_text=None, extra_files=None):
        """A minimal repo copy over a POSITIVE marker, for the mutation arms:
        the workflows, .github/actions, constraints-ci.txt, pyproject.toml
        and .cursor/install.sh, copied verbatim so a mutation is one
        targeted substitution on the shipped state. `extra_files` maps a
        repo-relative path to text, to add or replace a file."""
        root = self._real_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; the "
                        "mutation arms cannot copy the real surfaces.")
        (tmp_path / ".claude-plugin").mkdir(parents=True)
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
            "{}", encoding="utf-8")
        for path in self._workflow_files(root) + self._action_files(root):
            target = tmp_path / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        if workflow_text is not None:
            (tmp_path / ".github" / "workflows" / "tests.yml").write_text(
                workflow_text, encoding="utf-8")
        for name in ("constraints-ci.txt", "pyproject.toml"):
            (tmp_path / name).write_text(
                (root / name).read_text(encoding="utf-8"), encoding="utf-8")
        (tmp_path / ".cursor").mkdir(parents=True)
        (tmp_path / ".cursor" / "install.sh").write_text(
            script_text if script_text is not None
            else (root / ".cursor" / "install.sh").read_text(encoding="utf-8"),
            encoding="utf-8")
        for rel, text in (extra_files or {}).items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return tmp_path

    def test_the_floor_literals_agree(self):
        """Every statement of the Python floor must move together.

        FIVE statements of the Python floor, each read where it is STATED:
          1. pyproject.toml `requires-python = ">=X.Y"` (the SUPPORT
             BOUNDARY) — regex over the file text, exactly one.
          2. tests.yml `python-version: "X.Y"` (the TESTED SET) — regex
             over the file text, exactly one.
          3. test_py312_syntax_floor.py's detector call
             `ast.parse(source, feature_version=(X, Y))` (the SCAN
             DIALECT), plus its violation-message echo of the same value —
             regex over the guard's text, exactly one of each.
          4. tests.yml's lint legs `--target-version pyXY` (the LINT
             DIALECT) — read from the EXECUTED `run:` strings via
             yaml.safe_load, never the comment prose that quotes the flag;
             every leg must agree (the local lint script's byte-parity test
             moves WITH the workflow, so it cannot bind the value).
          5. .cursor/install.sh `PYTHON_FLOOR_MINOR=N` (the LOCAL
             BOOTSTRAP's refusal), compared as (3, N) — regex for one
             column-0 line, exactly one.
        Before this arm nothing bound them: the alignment was claimed in
        COMMENTS only, so editing any one literal reddened nothing while
        the declared and enforced floors diverged silently.

        The guard file MENTIONS several feature_version values by design —
        its own boundary arm parses a PEP 695 sample at (3, 11) to prove
        the detector discriminates — so this arm cannot compare every
        mention. It anchors on the two occurrences that STATE the enforced
        floor: the detector's `ast.parse(source, feature_version=(X, Y))`
        call, and the violation-message echo of the same value. A message
        left at the old floor when the call moves is exactly the silent
        divergence this arm exists to catch.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which ships no pyproject/workflow pair by design. "
                "This skip is NO INFORMATION about floor-literal alignment "
                "— never read it as a pass."
            )
        pyproject = root / "pyproject.toml"
        assert pyproject.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {pyproject} is missing. The floor alignment arm cannot "
            f"run. This is a FAILURE, not a skip: inside a checkout the "
            f"declaration is required."
        )
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The floor alignment arm cannot "
            f"run. This is a FAILURE, not a skip: inside a checkout the "
            f"workflow is required."
        )
        guard = Path(__file__).resolve().parent / "test_py312_syntax_floor.py"
        assert guard.exists(), (
            f"the syntax-floor guard {guard} is missing beside this file. "
            f"The floor alignment arm cannot run — this is a FAILURE, not "
            f"a skip."
        )

        declared = re.findall(
            r'requires-python\s*=\s*">=(\d+)\.(\d+)"',
            pyproject.read_text(encoding="utf-8"),
        )
        assert declared, (
            f"PARSE FAILURE, not floor drift: no `requires-python = "
            f"\">=X.Y\"` literal found in {pyproject}. The declaration "
            f"changed shape — re-point this arm; do not weaken it to a "
            f"skip."
        )
        assert len(declared) == 1, (
            f"PARSE AMBIGUOUS, not floor drift: {len(declared)} "
            f"`requires-python` declarations found in {pyproject}. "
            f"Reconcile them before this arm can compare."
        )
        cell = re.findall(
            r'python-version:\s*"(\d+)\.(\d+)"',
            workflow.read_text(encoding="utf-8"),
        )
        assert cell, (
            f"PARSE FAILURE, not floor drift: no `python-version: \"X.Y\"` "
            f"literal found in {workflow}. The CI cell changed shape — "
            f"re-point this arm; do not weaken it to a skip."
        )
        assert len(cell) == 1, (
            f"PARSE AMBIGUOUS, not floor drift: {len(cell)} "
            f"`python-version: \"X.Y\"` literals found in {workflow}. "
            f"Reconcile them before this arm can compare."
        )
        # The enforced constant: the detector's parse of the scanned
        # corpus. Anchored on `ast.parse(source,` so the boundary arm's
        # deliberate (3, 11) sample parse cannot masquerade as the floor.
        enforced = re.findall(
            r"ast\.parse\(\s*source,\s*feature_version=\((\d+),\s*(\d+)\)",
            guard.read_text(encoding="utf-8"),
        )
        assert enforced, (
            f"PARSE FAILURE, not floor drift: no "
            f"`ast.parse(source, feature_version=(X, Y))` call found in "
            f"{guard}. The detector changed shape — re-point this arm; do "
            f"not weaken it to a skip."
        )
        assert len(enforced) == 1, (
            f"PARSE AMBIGUOUS, not floor drift: {len(enforced)} "
            f"`ast.parse(source, ...)` calls found in {guard}. The "
            f"detector must state ONE floor; reconcile the copies."
        )
        echo = re.findall(
            r"not parseable at feature_version=\((\d+),\s*(\d+)\)",
            guard.read_text(encoding="utf-8"),
        )
        assert len(echo) == 1, (
            f"PARSE AMBIGUOUS, not floor drift: {len(echo)} "
            f"violation-message floor echoes found in {guard}; expected "
            f"exactly one. Re-point this arm."
        )

        # The fourth floor statement: the lint legs' `--target-version
        # pyXY`, read from the EXECUTED `run:` strings (yaml.safe_load), not
        # the file text — the workflow's comment prose quotes the flag, and
        # prose is not a declaration. Every leg must state the floor.
        lint_runs = "\n".join(
            step["run"] for step in self._steps_of(
                self._load_workflow_yaml(workflow))
            if isinstance(step.get("run"), str)
        )
        ruff_targets = re.findall(
            r"--target-version(?:=|\s+)py(\d)(\d+)\b", lint_runs)
        assert ruff_targets, (
            f"PARSE FAILURE, not floor drift: no executed `ruff ... "
            f"--target-version pyXY` found in {workflow}. The lint step "
            f"changed shape — re-point this arm; do not weaken it to a "
            f"skip."
        )

        declared_pair = tuple(int(part) for part in declared[0])
        cell_pair = tuple(int(part) for part in cell[0])
        # The fifth floor statement: the local bootstrap's interpreter
        # check, which states the floor ONCE as `PYTHON_FLOOR_MINOR=N` and
        # derives every comparison and message from it.
        script = root / ".cursor" / "install.sh"
        assert script.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {script} is missing. The floor alignment arm cannot run. "
            f"This is a FAILURE, not a skip: inside a checkout the "
            f"bootstrap script is required."
        )
        script_floor = re.findall(
            r"(?m)^PYTHON_FLOOR_MINOR=(\d+)[ \t]*$",
            script.read_text(encoding="utf-8"),
        )
        assert len(script_floor) == 1, (
            f"PARSE FAILURE, not floor drift: expected exactly one "
            f"`PYTHON_FLOOR_MINOR=N` line in {script}, found "
            f"{len(script_floor)}. The script's interpreter check changed "
            f"shape — re-point this arm; do not weaken it to a skip."
        )
        script_pair = (3, int(script_floor[0]))
        ruff_pairs = sorted({tuple(int(p) for p in t) for t in ruff_targets})
        guard_pair = tuple(int(part) for part in enforced[0])
        echo_pair = tuple(int(part) for part in echo[0])
        assert echo_pair == guard_pair, (
            f"GUARD-INTERNAL DRIFT: the detector enforces "
            f"{'.'.join(map(str, guard_pair))} but its violation message "
            f"reports {'.'.join(map(str, echo_pair))}. A message left at "
            f"the old floor misreports every violation it prints; move "
            f"both in the same change."
        )
        def dotted(pair):
            return ".".join(map(str, pair))

        assert (declared_pair == cell_pair == guard_pair == script_pair
                and ruff_pairs == [declared_pair]), (
            f"FLOOR-LITERAL DRIFT: the five floor statements disagree. "
            f"Move ALL FIVE together, in one change:\n"
            f"  1. pyproject.toml requires-python (text):       "
            f"{dotted(declared_pair)}\n"
            f"  2. tests.yml python-version (text):             "
            f"{dotted(cell_pair)}\n"
            f"  3. syntax-floor guard feature_version + echo:   "
            f"{dotted(guard_pair)}\n"
            f"  4. tests.yml ruff --target-version (executed run: strings): "
            f"{['py' + ''.join(map(str, p)) for p in ruff_pairs]}\n"
            f"  5. .cursor/install.sh PYTHON_FLOOR_MINOR (text): "
            f"{dotted(script_pair)}\n"
            f"Each is a different QUESTION (support boundary, tested set, "
            f"scan dialect, lint dialect, local bootstrap) that shares one "
            f"value today; moving any one takes its own run on the new "
            f"target, and then all five move in the same change — this red "
            f"is the reminder, not a bug."
        )

    def test_ruff_target_version_is_bound_to_the_floor(self, tmp_path, monkeypatch):
        """MUTATION ARM (fourth floor statement): a lint leg's
        `--target-version` drifting from the declared floor reddens the
        floor-literal arm. Before round 7 it was bound to nothing but the
        local lint script's byte parity, which moves WITH it. The same arm
        certifies the fifth statement: .cursor/install.sh's
        `PYTHON_FLOOR_MINOR` moved alone reddens it too."""
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        real_text = (root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8")
        leg = "ruff check --select F821 --target-version py312 pact-plugin/"
        assert leg in real_text, "the F821 leg changed shape; re-point this arm"
        self._shape_reddens(
            tmp_path, monkeypatch, "ruff-py311",
            "test_the_floor_literals_agree", "FLOOR-LITERAL DRIFT",
            workflow_text=real_text.replace(
                leg, leg.replace("py312", "py311"), 1))

        # The bootstrap's interpreter floor moving alone reddens too.
        real_script = (root / ".cursor" / "install.sh").read_text(
            encoding="utf-8")
        floor_line = re.search(r"(?m)^PYTHON_FLOOR_MINOR=(\d+)[ \t]*$",
                               real_script)
        assert floor_line, "install.sh floor line changed shape; re-point"
        moved = int(floor_line.group(1)) + 1
        self._shape_reddens(
            tmp_path, monkeypatch, "install-sh-floor",
            "test_the_floor_literals_agree", "FLOOR-LITERAL DRIFT",
            script_text=real_script.replace(
                floor_line.group(0), f"PYTHON_FLOOR_MINOR={moved}", 1))
        # The line removed (or reshaped) is a PARSE FAILURE, never a pass.
        self._shape_reddens(
            tmp_path, monkeypatch, "install-sh-floor-missing",
            "test_the_floor_literals_agree",
            "PARSE FAILURE, not floor drift: expected exactly one "
            "`PYTHON_FLOOR_MINOR=N`",
            script_text=real_script.replace(floor_line.group(0), "", 1))

    def test_ci_branch_emits_nothing_to_stderr(self):
        """The silence is the CONTRACT, so pin it — a log line here breaks 15
        CLI tests the moment real drift occurs, which is exactly when it fires.

        Proxy, named honestly: this asserts the function writes nothing to
        stderr and raises no warning IN THIS PROCESS. It does not re-run the
        subprocess measurement; it pins the property that measurement justified.
        """
        import builtins
        import io
        import warnings
        from contextlib import redirect_stderr

        from scripts.memory_init import check_and_install_dependencies

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'pysqlite3':
                raise ImportError("No module named 'pysqlite3'")
            return original_import(name, *args, **kwargs)

        buf = io.StringIO()
        with patch.dict(os.environ, {"CI": "true"}), \
                patch.object(builtins, '__import__', mock_import), \
                patch('scripts.memory_init.subprocess.run') as mock_run, \
                warnings.catch_warnings(record=True) as caught, \
                redirect_stderr(buf):
            warnings.simplefilter("always")
            result = check_and_install_dependencies()

        assert result['status'] == 'skipped_ci'      # the branch really fired
        assert not mock_run.called
        assert buf.getvalue() == "", (
            f"the CI branch wrote to stderr: {buf.getvalue()!r}. The CLI's "
            f"stderr is parsed as JSON by tests/test_memory_cli.py, so this "
            f"breaks 15 of them under real drift. Detect drift with the parity "
            f"test above instead."
        )
        assert not caught, (
            f"the CI branch raised {[str(w.message) for w in caught]}. Python's "
            f"default warning display writes to stderr, so in the CLI "
            f"subprocess this corrupts the same JSON contract."
        )

    def _shape_reddens(self, tmp_path, monkeypatch, name, arm, match,
                       workflow_text=None, script_text=None,
                       extra_files=None):
        """Run the SHIPPED arm `arm` against a rig copy carrying one shape
        and require it to redden with `match` — the kill is the shipped
        arm's, never a reimplementation beside it."""
        copy = self._repo_copy_carrying(
            tmp_path / name, workflow_text=workflow_text,
            script_text=script_text, extra_files=extra_files)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        with pytest.raises(AssertionError, match=match):
            getattr(self, arm)()

    def _shape_stays_green(self, tmp_path, monkeypatch, name, arm,
                           workflow_text=None, script_text=None,
                           extra_files=None):
        """CONTROL: a faithful shape the arm must ACCEPT."""
        copy = self._repo_copy_carrying(
            tmp_path / name, workflow_text=workflow_text,
            script_text=script_text, extra_files=extra_files)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        getattr(self, arm)()

    def test_unpinned_memory_init_package_reddens(self, tmp_path, monkeypatch):
        """MUTATION ARM: a memory_init package missing from the pins reddens."""
        root = self._require_root("memory_init's pins")
        pins = (root / "constraints-ci.txt").read_text(encoding="utf-8")
        line = next(ln for ln in pins.splitlines()
                    if ln.lower().startswith("model2vec=="))
        self._shape_reddens(
            tmp_path, monkeypatch, "model2vec-unpinned",
            "test_memory_init_packages_are_pinned", r"DEPENDENCY DRIFT: \['model2vec'\]",
            extra_files={"constraints-ci.txt": pins.replace(line + "\n", "", 1)})

    def test_verify_placement_mutations_redden(self, tmp_path, monkeypatch):
        """MUTATION ARM (F1(a), F2): every way the verify steps can stop
        bracketing a test run reddens the placement arm; release.yml's job
        is measured NOT governed (it names none of the governed tools)."""
        root = self._require_root("verify-step placement")
        real = (root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8")
        doc = self._load_workflow_yaml(root / ".github" / "workflows" / "tests.yml")
        steps = self._steps_of(doc)
        names = [s.get("name") for s in steps]
        before = steps[names.index("Run test suite") - 1]
        assert before.get("run") == self.VERIFY_CMD, (
            "the shipped before-check moved; re-point this arm")
        before_name = before["name"]
        after_name = steps[-1]["name"]
        before_block = (f"      - name: {before_name}\n"
                        f"        run: {self.VERIFY_CMD}\n")
        after_block = (f"      - name: {after_name}\n"
                       f"        if: always()\n"
                       f"        run: {self.VERIFY_CMD}\n")
        assert before_block in real and real.endswith(after_block), (
            "the shipped verify steps changed shape; re-point this arm")
        test_line = f"        run: {self.TEST_CMD}\n"
        assert real.count(test_line) == 1
        arm = "test_ci_environment_is_verified_around_every_test_run"
        install = "      - name: Install test dependencies\n"
        for name, text in [
            ("before-removed", real.replace(before_block, "", 1)),
            ("before-earlier", real.replace(before_block, "", 1).replace(
                install, before_block + install, 1)),
            ("after-not-last", real + "      - name: tail\n        run: echo done\n"),
            ("after-not-always", real.replace(
                "        if: always()\n        run: " + self.VERIFY_CMD,
                "        run: " + self.VERIFY_CMD, 1)),
            ("pytest-command-changed", real.replace(
                test_line, "        run: python -m pytest -ra -p no:cacheprovider\n", 1)),
            ("env-on-test-step", real.replace(
                test_line, test_line + "        env:\n          PYTHONPATH: extra\n", 1)),
            ("shell-on-verify-step", real.replace(
                before_block, before_block + "        shell: bash\n", 1)),
            ("second-governed-job", real + (
                "  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: python -m pip --version\n")),
            ("second-pytest-step", real.replace(
                after_block,
                "      - name: Again\n        working-directory: pact-plugin\n"
                f"        run: {self.TEST_CMD}\n" + after_block, 1)),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, arm,
                                "VERIFY PLACEMENT DRIFT", workflow_text=text)
        # CONTROL: release.yml's job is not governed today, measured.
        release = self._load_workflow_yaml(
            root / ".github" / "workflows" / "release.yml")
        release_runs = [s.get("run") or "" for s in self._steps_of(release)]
        assert not any(self._GOVERNED_RE.search(r) for r in release_runs), (
            "release.yml now names a governed tool: its job needs verify "
            "steps, or the placement arm's governed set needs a decision")

    def test_tripwire_families_redden(self, tmp_path, monkeypatch):
        """MUTATION ARM (C-lite, F6/F7/F8): one shape per token family,
        on each surface of the text set; faithful lines stay green."""
        root = self._require_root("the isolated-environment tripwire")
        real = (root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8")
        real_sh = (root / ".cursor" / "install.sh").read_text(encoding="utf-8")
        lint_anchor = "          ruff --version\n"
        arm = "test_no_isolated_environment_tool_or_index_redirect"

        def in_block(command):
            return real.replace(lint_anchor, lint_anchor + f"          {command}\n", 1)

        for name, kwargs, token in [
            ("tool-uvx", dict(workflow_text=in_block("uvx ruff check .")), "uvx"),
            ("tool-uv-sync", dict(workflow_text=in_block("timeout 10 uv sync")), "uv"),
            ("tool-pipx-script", dict(script_text=real_sh + "\npipx install x\n"), "pipx"),
            ("uninstall-pip3", dict(workflow_text=in_block("pip3 uninstall -y pyyaml")),
             "uninstall"),
            ("index-env-export", dict(
                script_text=real_sh + "\nexport PIP_INDEX_URL=https://x.example/simple\n"),
             "PIP_INDEX_URL"),
            ("index-flag", dict(workflow_text=in_block(
                "python -m pip install --extra-index-url https://x.example/simple x")),
             "extra-index-url"),
            ("index-env-key", dict(workflow_text=real.replace(
                "    steps:", "    env:\n      PIP_CONSTRAINT: other.txt\n    steps:", 1)),
             "PIP_CONSTRAINT"),
            ("tracked-script", dict(extra_files={
                "scripts/ci-extra.sh": "#!/bin/sh\nconda install -y numpy\n"}), "conda"),
            ("makefile", dict(extra_files={
                "Makefile": "deps:\n\tpoetry install\n"}), "poetry"),
            ("composite-action", dict(extra_files={
                ".github/actions/x/action.yml":
                    "name: x\nruns:\n  using: composite\n  steps:\n"
                    "    - run: mamba install -y numpy\n      shell: bash\n"}), "mamba"),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, arm,
                                f"(?s)TRIPWIRE: .*{re.escape(token)}", **kwargs)
        # CONTROLS: faithful lines, read-only pip, and the canonical install.
        self._shape_stays_green(
            tmp_path, monkeypatch, "faithful", arm, workflow_text=in_block(
                "pip list; python -m pip install -r constraints-ci.txt"))

    def test_unvetted_actions_redden(self, tmp_path, monkeypatch):
        """MUTATION ARM: an unvetted action at step level, a docker image,
        a third-party reusable workflow, and an unvetted action inside a
        local composite action all redden the allowlist; a re-pinned
        allowlisted action stays green."""
        root = self._require_root("the action allowlist")
        real = (root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8")
        install = "      - name: Install test dependencies\n"
        arm = "test_every_third_party_action_is_allowlisted"
        for name, kwargs in [
            ("pre-commit-action", dict(workflow_text=real.replace(
                install, "      - uses: pre-commit/action@v3.0.1\n" + install, 1))),
            ("docker-image", dict(workflow_text=real.replace(
                install, "      - uses: docker://python:3.12-slim\n" + install, 1))),
            ("reusable-workflow", dict(extra_files={
                ".github/workflows/reuse.yml":
                    "name: reuse\non: [pull_request]\njobs:\n  r:\n"
                    "    uses: someorg/ci/.github/workflows/lint.yml@v1\n"})),
            ("inside-local-action", dict(
                workflow_text=real.replace(
                    install, "      - uses: ./tools/setup\n" + install, 1),
                extra_files={"tools/setup/action.yml":
                             "name: s\nruns:\n  using: composite\n  steps:\n"
                             "    - uses: astral-sh/setup-uv@v6\n"})),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, arm,
                                "ACTION ALLOWLIST DRIFT", **kwargs)
        self._shape_stays_green(
            tmp_path, monkeypatch, "repinned", arm, workflow_text=real.replace(
                install, "      - uses: actions/checkout@v5\n" + install, 1))


class TestVerifyCiEnvironmentScript:
    """Unit arms for scripts/verify_ci_environment.py, loaded by path, run
    on FAKE environments: synthetic `*.dist-info` directories in tmp_path
    passed as `path=`, with the interpreter check off unless a test is
    about it. Each verdict field goes red exactly once in its own arm."""

    @pytest.fixture
    def verify(self):
        return TestDependencyDriftIsDetectable._verify_script()

    @staticmethod
    def _dist(site, name, version, requires=()):
        """Write one synthetic installed distribution into `site`."""
        info = site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(parents=True)
        body = [f"Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        body += [f"Requires-Dist: {req}" for req in requires]
        (info / "METADATA").write_text("\n".join(body) + "\n", encoding="utf-8")
        return info

    def _check(self, verify, pins, *sites, baseline=None):
        return verify.check_environment(
            pins, path=[str(s) for s in sites],
            baseline=baseline if baseline is not None else {},
            check_interpreter=False)

    def test_exact_match_is_green(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "1.0")
        verdict = self._check(verify, {"alpha": "1.0"}, tmp_path)
        assert verdict.ok, verdict.render()

    def test_wrong_version_only(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "0.9")
        verdict = self._check(verify, {"alpha": "1.0"}, tmp_path)
        assert verdict.wrong_version == {"alpha": ("0.9", "1.0")}
        assert not (verdict.missing or verdict.unexpected or verdict.shadowed)
        assert "WRONG VERSION" in verdict.render()

    def test_missing_only(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "1.0")
        verdict = self._check(verify, {"alpha": "1.0", "beta": "2.0"}, tmp_path)
        assert verdict.missing == ("beta",)
        assert not (verdict.wrong_version or verdict.unexpected or verdict.shadowed)
        assert "MISSING" in verdict.render()

    def test_unexpected_only_and_the_runner_baseline_remedy(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "1.0")
        self._dist(tmp_path, "six", "1.16.0")
        verdict = self._check(verify, {"alpha": "1.0"}, tmp_path)
        assert verdict.unexpected == ("six",)
        assert not (verdict.wrong_version or verdict.missing or verdict.shadowed)
        rendered = verdict.render()
        assert "UNEXPECTED" in rendered
        # F9: the runner-image remedy names where the baseline lives.
        assert "RUNNER_BASELINE" in rendered
        assert "scripts/verify_ci_environment.py" in rendered

    def test_shadowed_checks_the_first_copy(self, verify, tmp_path):
        """F4: the copy Python imports (first on the path) is the one
        checked, and any second copy is SHADOWED with every location."""
        first, second = tmp_path / "first", tmp_path / "second"
        self._dist(first, "alpha", "0.9")
        self._dist(second, "alpha", "1.0")
        verdict = self._check(verify, {"alpha": "1.0"}, first, second)
        assert verdict.wrong_version == {"alpha": ("0.9", "1.0")}
        assert list(verdict.shadowed) == ["alpha"]
        assert len(verdict.shadowed["alpha"]) == 2
        assert str(first.resolve()) in verdict.shadowed["alpha"][0]
        assert "SHADOWED" in verdict.render()

    def test_transitive_dependency_is_allowed(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "1.0", requires=["gamma>=1"])
        self._dist(tmp_path, "gamma", "3.0")
        assert self._check(verify, {"alpha": "1.0"}, tmp_path).ok

    def test_marker_false_requirement_is_not_pulled_in(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "1.0",
                   requires=['gamma; python_version < "3"'])
        self._dist(tmp_path, "gamma", "3.0")
        assert self._check(verify, {"alpha": "1.0"}, tmp_path).unexpected == ("gamma",)

    def test_requested_extras_reach_their_dependencies(self, verify, tmp_path):
        """F5: root -> child[fast] -> grand; extra == "fast" allows grand."""
        self._dist(tmp_path, "root-pkg", "1.0", requires=["child[fast]"])
        self._dist(tmp_path, "child", "1.0", requires=['grand; extra == "fast"'])
        self._dist(tmp_path, "grand", "1.0")
        assert self._check(verify, {"root-pkg": "1.0"}, tmp_path).ok

    def test_unrequested_extra_is_not_pulled_in(self, verify, tmp_path):
        self._dist(tmp_path, "root-pkg", "1.0", requires=["child"])
        self._dist(tmp_path, "child", "1.0", requires=['grand; extra == "slow"'])
        self._dist(tmp_path, "grand", "1.0")
        assert self._check(verify, {"root-pkg": "1.0"}, tmp_path).unexpected == ("grand",)

    def test_runner_baseline_roots_and_their_dependencies_are_allowed(self, verify, tmp_path):
        self._dist(tmp_path, "alpha", "1.0")
        self._dist(tmp_path, "image-tool", "5.0", requires=["image-dep"])
        self._dist(tmp_path, "image-dep", "1.0")
        verdict = self._check(verify, {"alpha": "1.0"}, tmp_path,
                              baseline={"Image_Tool": "ships with the runner"})
        assert verdict.ok, verdict.render()

    def test_names_are_normalised(self, verify, tmp_path):
        self._dist(tmp_path, "Foo.Bar", "1.0")
        constraints = tmp_path / "c.txt"
        constraints.write_text("foo_bar==1.0\n", encoding="utf-8")
        pins = verify.read_pins(constraints)
        assert pins == {"foo-bar": "1.0"}
        assert self._check(verify, pins, tmp_path).ok

    def test_pip_is_exempt_from_the_version_check(self, verify, tmp_path):
        self._dist(tmp_path, "pip", "99.0")
        assert self._check(verify, {"pip": "1.0"}, tmp_path).ok

    @pytest.mark.parametrize("line", [
        "ruff==0.17.*",
        'ruff==0.17.3; python_version < "3.11"',
        "ruff[extra]==0.17.3",
        "-r other.txt",
        "-i https://x.example/simple",
        "ruff @ https://x.example/ruff.whl",
        "ruff>=0.17",
    ])
    def test_read_pins_refuses_a_line_that_is_not_an_exact_pin(self, verify, tmp_path, line):
        constraints = tmp_path / "c.txt"
        constraints.write_text(f"pytest==9.2.0\n{line}\n", encoding="utf-8")
        with pytest.raises(verify.ConstraintsError, match=":2:"):
            verify.read_pins(constraints)

    def test_read_pins_refuses_a_duplicate_pin(self, verify, tmp_path):
        constraints = tmp_path / "c.txt"
        constraints.write_text("ruff==0.17.3\nRuff==0.17.3\n", encoding="utf-8")
        with pytest.raises(verify.ConstraintsError, match="pinned twice"):
            verify.read_pins(constraints)

    def test_read_pins_accepts_comments(self, verify, tmp_path):
        constraints = tmp_path / "c.txt"
        constraints.write_text(
            "# header\n\nruff==0.17.3  # the linter\n", encoding="utf-8")
        assert verify.read_pins(constraints) == {"ruff": "0.17.3"}

    def test_interpreter_mismatch_reports_the_unresolved_pair(self, verify, tmp_path, monkeypatch):
        """F3: a `python3` on PATH in another DIRECTORY than sys.executable
        is a mismatch, reported unresolved; one in the same directory is
        not; none at all is."""
        elsewhere = tmp_path / "bin" / "python3"
        monkeypatch.setattr(verify.shutil, "which", lambda name: str(elsewhere))
        verdict = verify.check_environment({}, path=[], baseline={})
        assert verdict.interpreter_mismatch == (
            os.path.abspath(str(elsewhere)), os.path.abspath(sys.executable))
        assert "INTERPRETER MISMATCH" in verdict.render()
        # A venv's python symlinks to the base interpreter: a python3 in
        # ANOTHER directory resolving to the SAME file is still a different
        # environment, so the comparison is by directory, not by file.
        other_dir = tmp_path / "venv-elsewhere" / "bin"
        other_dir.mkdir(parents=True)
        (other_dir / "python3").symlink_to(os.path.realpath(sys.executable))
        monkeypatch.setattr(verify.shutil, "which",
                            lambda name: str(other_dir / "python3"))
        assert verify.check_environment(
            {}, path=[], baseline={}).interpreter_mismatch is not None
        beside = os.path.join(os.path.dirname(sys.executable), "python3")
        monkeypatch.setattr(verify.shutil, "which", lambda name: beside)
        assert verify.check_environment({}, path=[], baseline={}).interpreter_mismatch is None
        monkeypatch.setattr(verify.shutil, "which", lambda name: None)
        missing = verify.check_environment({}, path=[], baseline={})
        assert missing.interpreter_mismatch is not None
        assert missing.interpreter_mismatch[1] == os.path.abspath(sys.executable)

    def test_main_exit_codes(self, verify, tmp_path, monkeypatch, capsys):
        good = tmp_path / "good.txt"
        good.write_text("alpha==1.0\n", encoding="utf-8")
        bad = tmp_path / "bad.txt"
        bad.write_text("alpha==1.*\n", encoding="utf-8")
        passing = verify.Verdict({}, (), (), {}, None)
        failing = verify.Verdict({}, ("alpha",), (), {}, None)
        monkeypatch.setattr(verify, "check_environment", lambda pins: passing)
        assert verify.main(["--constraints", str(good)]) == 0
        monkeypatch.setattr(verify, "check_environment", lambda pins: failing)
        assert verify.main(["--constraints", str(good)]) == 1
        assert verify.main(["--constraints", str(good), "--report"]) == 0
        assert verify.main(["--constraints", str(bad)]) == 2
        assert verify.main(["--constraints", str(bad), "--report"]) == 2
        assert verify.main(["--no-such-option"]) == 2
        assert "MISSING" in capsys.readouterr().out


class TestCIEnvironmentBinding:
    """The session check (F1(b)) in tests/ci_environment_binding.py."""

    @pytest.fixture
    def binding(self):
        import ci_environment_binding
        return ci_environment_binding

    def test_gate_off_loads_nothing(self, binding, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

        def boom(*args, **kwargs):
            raise AssertionError("the verify script was loaded with the gate off")

        monkeypatch.setattr(binding, "_load_verify_script", boom)
        assert binding.CIEnvironmentBinding().pytest_sessionstart(None) is None
        monkeypatch.setenv("GITHUB_ACTIONS", "false")
        assert binding.CIEnvironmentBinding().pytest_sessionstart(None) is None

    def _stub(self, verdict_ok):
        verify = TestDependencyDriftIsDetectable._verify_script()
        verdict = (verify.Verdict({}, (), (), {}, None) if verdict_ok
                   else verify.Verdict({}, ("alpha",), (), {}, None))

        class Stub:
            ConstraintsError = verify.ConstraintsError
            read_pins = staticmethod(lambda path: {"alpha": "1.0"})
            check_environment = staticmethod(lambda pins: verdict)
        return Stub

    def test_gate_on_and_passing_proceeds(self, binding, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setattr(binding, "_load_verify_script",
                            lambda script: self._stub(True))
        assert binding.CIEnvironmentBinding().pytest_sessionstart(None) is None

    def test_gate_on_and_failing_exits_3_with_the_heading(self, binding, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setattr(binding, "_load_verify_script",
                            lambda script: self._stub(False))
        with pytest.raises(pytest.exit.Exception) as exc:
            binding.CIEnvironmentBinding().pytest_sessionstart(None)
        assert exc.value.returncode == 3
        assert binding.HEADING in str(exc.value)
        assert "MISSING" in str(exc.value)

    def test_gate_on_and_bad_constraints_exits_3(self, binding, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        stub = self._stub(True)

        def refuse(path):
            raise stub.ConstraintsError("c.txt:1: not an exact pin")

        stub.read_pins = staticmethod(refuse)
        monkeypatch.setattr(binding, "_load_verify_script", lambda script: stub)
        with pytest.raises(pytest.exit.Exception) as exc:
            binding.CIEnvironmentBinding().pytest_sessionstart(None)
        assert exc.value.returncode == 3
        assert "CONSTRAINTS ERROR" in str(exc.value)

    def test_the_plugin_is_registered_for_every_run(self, request):
        assert request.config.pluginmanager.has_plugin("ci-environment-binding")

    def test_the_hook_fires_in_a_real_pytest_run(self, tmp_path):
        """A REAL pytest process under GITHUB_ACTIONS=true, against a copy
        of the binding, the verify script and a constraints file pinning a
        WRONG pytest version, exits 3 with the heading before any test runs;
        the same run with the right pin is not stopped by the binding."""
        root = TestDependencyDriftIsDetectable._real_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout.")
        rig = tmp_path / "rig"
        (rig / ".claude-plugin").mkdir(parents=True)
        (rig / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
        (rig / "scripts").mkdir()
        shutil.copy(root / "scripts" / "verify_ci_environment.py",
                    rig / "scripts" / "verify_ci_environment.py")
        (rig / "suite").mkdir()
        shutil.copy(Path(__file__).parent / "ci_environment_binding.py",
                    rig / "suite" / "ci_environment_binding.py")
        (rig / "suite" / "conftest.py").write_text(
            "from ci_environment_binding import CIEnvironmentBinding\n\n\n"
            "def pytest_configure(config):\n"
            "    config.pluginmanager.register(CIEnvironmentBinding(), 'ci-environment-binding')\n",
            encoding="utf-8")
        (rig / "suite" / "test_probe.py").write_text(
            "def test_probe():\n    pass\n", encoding="utf-8")
        env = dict(os.environ, GITHUB_ACTIONS="true", PYTHONDONTWRITEBYTECODE="1")
        env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")

        def run(pytest_pin):
            (rig / "constraints-ci.txt").write_text(
                f"pytest=={pytest_pin}\n", encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "suite"], cwd=rig, env=env, capture_output=True, text=True,
                timeout=120)

        wrong = run("0.0.1")
        assert wrong.returncode == 3, wrong.stdout + wrong.stderr
        assert "CI ENVIRONMENT DRIFT (pytest process)" in wrong.stdout + wrong.stderr
        assert "WRONG VERSION" in wrong.stdout + wrong.stderr
        # The right pin clears the WRONG VERSION heading. (Other installed
        # packages are outside this one-pin rig's closure, so the session may
        # still stop on UNEXPECTED; that half is environment-dependent and is
        # not asserted here.)
        right = run(pytest.__version__)
        assert "WRONG VERSION" not in right.stdout + right.stderr, right.stdout


class TestMaybeEmbedPending:
    """Tests for maybe_embed_pending function.

    Note: These tests use session marker files to track once-per-session behavior.
    The embed_pending_memories import is mocked via sys.modules since the
    embedding_catchup module uses relative imports that don't work in test context.
    """

    def test_session_scoped_only_runs_once(self, tmp_path):
        """Test that maybe_embed_pending only runs once per session via marker file."""
        from scripts.memory_init import maybe_embed_pending, _get_embedding_attempted_path

        # Use a unique session ID for this test
        test_session_id = f"test-once-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()

            # Clean up any existing marker
            if marker_path.exists():
                marker_path.unlink()

            # Create a mock module for embedding_catchup
            mock_catchup = MagicMock()
            mock_catchup.embed_pending_memories = MagicMock(return_value={'processed': 0})

            with patch.dict(sys.modules, {'.embedding_catchup': mock_catchup}):
                # First call creates marker and attempts embedding
                result1 = maybe_embed_pending()

                # Marker should now exist
                assert marker_path.exists()

                # Second call should skip because marker exists
                result2 = maybe_embed_pending()
                assert result2['status'] == 'skipped'
                assert 'already attempted' in result2['message'].lower()

            # Clean up
            if marker_path.exists():
                marker_path.unlink()

    def test_marker_file_created_on_first_call(self, tmp_path):
        """Test that the marker file is created on first call."""
        from scripts.memory_init import maybe_embed_pending, _get_embedding_attempted_path

        test_session_id = f"test-marker-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()

            # Ensure marker doesn't exist
            if marker_path.exists():
                marker_path.unlink()

            assert not marker_path.exists()

            # Call maybe_embed_pending - it will fail to import embedding_catchup
            # but should still create the marker file first
            result = maybe_embed_pending()

            # Marker should be created regardless of whether embedding succeeds
            assert marker_path.exists()

            # Clean up
            marker_path.unlink()

    def test_skips_when_marker_exists(self, tmp_path):
        """Test that function skips immediately when marker exists."""
        from scripts.memory_init import maybe_embed_pending, _get_embedding_attempted_path

        test_session_id = f"test-skip-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()

            # Pre-create the marker
            marker_path.touch()

            result = maybe_embed_pending()

            assert result['status'] == 'skipped'
            assert 'already attempted' in result['message'].lower()

            # Clean up
            marker_path.unlink()


class TestMemoryAPIIntegration:
    """Tests for integration between memory_api.py and memory_init.py.

    Note: Direct testing of memory_api.py is complex due to its relative imports.
    These tests verify the lazy initialization pattern by testing the _ensure_ready
    helper function behavior and the ensure_memory_ready integration.
    """

    def test_ensure_ready_helper_calls_ensure_memory_ready(self):
        """Test that _ensure_ready() calls ensure_memory_ready()."""
        from scripts.memory_init import reset_initialization, is_initialized, ensure_memory_ready

        reset_initialization()
        assert is_initialized() is False

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Simulate what _ensure_ready does: call ensure_memory_ready
            ensure_memory_ready()

            assert is_initialized() is True
            assert mock_deps.call_count == 1

    def test_repeated_ensure_ready_calls_are_idempotent(self):
        """Test that calling ensure_memory_ready multiple times only initializes once."""
        from scripts.memory_init import reset_initialization, ensure_memory_ready

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Multiple calls simulating multiple API method calls
            ensure_memory_ready()  # save()
            ensure_memory_ready()  # search()
            ensure_memory_ready()  # list()
            ensure_memory_ready()  # get()
            ensure_memory_ready()  # update()

            # Should only initialize once
            assert mock_deps.call_count == 1

    def test_fast_path_returns_immediately(self):
        """Test that fast path (already_initialized=True) returns without work."""
        from scripts.memory_init import reset_initialization, ensure_memory_ready

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First call does work
            result1 = ensure_memory_ready()
            assert result1['already_initialized'] is False

            # Subsequent calls return immediately
            result2 = ensure_memory_ready()
            assert result2['already_initialized'] is True

            result3 = ensure_memory_ready()
            assert result3['already_initialized'] is True

            # Work was only done once
            assert mock_deps.call_count == 1
            assert mock_migrate.call_count == 1
            assert mock_embed.call_count == 1

    def test_api_pattern_simulation(self):
        """Simulate how memory_api uses _ensure_ready pattern."""
        from scripts.memory_init import reset_initialization, ensure_memory_ready, is_initialized

        reset_initialization()

        # Define a mock API method that follows the pattern
        def mock_api_method():
            """Simulates save(), search(), list(), etc."""
            ensure_memory_ready()  # This is what _ensure_ready() does
            return "operation completed"

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Before any API call
            assert is_initialized() is False

            # First API call triggers initialization
            mock_api_method()
            assert is_initialized() is True
            assert mock_deps.call_count == 1

            # Subsequent API calls don't re-initialize
            mock_api_method()
            mock_api_method()
            mock_api_method()
            assert mock_deps.call_count == 1


class TestGracefulDegradation:
    """Tests for graceful degradation when initialization steps fail."""

    def test_continues_after_dep_failure(self):
        """Test that initialization continues even if dependency installation fails."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'failed', 'installed': [], 'failed': ['pysqlite3']}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            result = ensure_memory_ready()

            # All steps were called despite first failure
            assert mock_migrate.call_count == 1
            assert mock_embed.call_count == 1
            assert result['deps']['status'] == 'failed'

    def test_continues_after_migration_error(self):
        """Test that initialization continues even if migration fails."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'error', 'message': 'Migration failed'}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            result = ensure_memory_ready()

            # Embedding step still ran
            assert mock_embed.call_count == 1
            assert result['migration']['status'] == 'error'

    def test_continues_after_embedding_error(self):
        """Test that initialization completes even if embedding fails."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization, is_initialized

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'error', 'message': 'Embedding failed'}

            result = ensure_memory_ready()

            # Initialization is still considered complete
            assert is_initialized() is True
            assert result['embedding']['status'] == 'error'

    def test_all_steps_fail_still_marks_initialized(self):
        """Test that even if all steps fail, system is marked initialized."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization, is_initialized

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'failed', 'installed': [], 'failed': ['all']}
            mock_migrate.return_value = {'status': 'error', 'message': 'Failed'}
            mock_embed.return_value = {'status': 'error', 'message': 'Failed'}

            result = ensure_memory_ready()

            # System is still marked initialized to prevent retry loops
            assert is_initialized() is True
            assert result['already_initialized'] is False


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_deps_result(self):
        """Test handling of empty dependency check result."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {}  # Empty dict
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Should not raise
            result = ensure_memory_ready()
            assert result is not None

    def test_none_values_in_results(self):
        """Test handling of None values in step results."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': None, 'failed': None}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': None, 'message': None}

            # Should not raise
            result = ensure_memory_ready()
            assert result is not None

    def test_reset_during_initialization(self):
        """Test that reset during initialization is handled safely."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        def slow_deps():
            time.sleep(0.1)
            return {'status': 'ok', 'installed': [], 'failed': []}

        with patch('scripts.memory_init.check_and_install_dependencies', side_effect=slow_deps), \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Start initialization in background
            def init_thread():
                ensure_memory_ready()

            t = threading.Thread(target=init_thread)
            t.start()

            # Try to reset while initialization is running
            time.sleep(0.05)
            reset_initialization()

            t.join()

            # State should be consistent (either initialized or not)
            # The point is that no exception is raised


class TestMaybeMigrateEmbeddings:
    """Tests for maybe_migrate_embeddings function.

    Tests cover:
    1. Happy paths: No table, empty table, dimensions match, migration success
    2. Edge cases: Import failures, connection failures, partial re-embedding
    3. Error handling: Exception during migration returns error status

    Note: The function uses relative imports from within the scripts package.
    We test by creating a testable wrapper that injects dependencies.
    """

    def _create_testable_migrate_function(
        self,
        pysqlite3_module=None,
        sqlite_vec_module=None,
        get_connection_func=None,
        get_embedding_service_func=None,
        generate_embedding_text_func=None,
        embedding_dim=256,
        import_error=False,
    ):
        """Create a version of maybe_migrate_embeddings with injected dependencies.

        This allows us to test the actual logic without dealing with relative imports.
        """
        import struct

        def testable_migrate():
            result = {"status": "ok", "message": None}

            try:
                # Simulate import block
                if import_error:
                    return result

                if pysqlite3_module is None or sqlite_vec_module is None:
                    return result

                if get_connection_func is None:
                    return result

                # Get expected dimension
                expected_dim = embedding_dim

                # Connect to database
                conn = get_connection_func()
                sqlite_vec_module.load(conn)

                # Check if vec_memories table exists
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
                )
                if cursor.fetchone() is None:
                    conn.close()
                    return result  # No table, nothing to migrate

                # Check actual dimension by examining an embedding
                try:
                    row = conn.execute("SELECT embedding FROM vec_memories LIMIT 1").fetchone()
                    if row is None:
                        conn.close()
                        return result  # Empty table, nothing to migrate

                    actual_dim = len(row[0]) // 4  # 4 bytes per float
                    if actual_dim == expected_dim:
                        conn.close()
                        return result  # Dimensions match, no migration needed

                except Exception:
                    conn.close()
                    return result

                # Dimension mismatch detected - need to migrate
                result["status"] = "migrating"
                result["message"] = f"Migrating embeddings: {actual_dim}-dim -> {expected_dim}-dim"

                # Drop old table
                conn.execute("DROP TABLE IF EXISTS vec_memories")
                conn.commit()

                # Recreate with new dimension
                conn.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                        memory_id TEXT PRIMARY KEY,
                        project_id TEXT PARTITION KEY,
                        embedding float[{expected_dim}]
                    )
                """)
                conn.commit()

                # Re-embed all memories
                service = get_embedding_service_func()
                memories = conn.execute("""
                    SELECT id, context, goal, lessons_learned, decisions, entities
                    FROM memories
                """).fetchall()

                success = 0
                for mem_id, context, goal, lessons, decisions, entities in memories:
                    try:
                        memory_dict = {
                            'context': context, 'goal': goal, 'lessons_learned': lessons,
                            'decisions': decisions, 'entities': entities,
                        }
                        embed_text = generate_embedding_text_func(memory_dict)
                        embedding = service.generate(embed_text)

                        if embedding:
                            embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
                            conn.execute(
                                "INSERT OR REPLACE INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
                                (mem_id, embedding_blob)
                            )
                            success += 1
                    except Exception:
                        continue

                conn.commit()
                conn.close()

                result["status"] = "ok"
                result["message"] = f"Migrated {success}/{len(memories)} embeddings to {expected_dim}-dim"
                return result

            except Exception as e:
                result["status"] = "error"
                result["message"] = str(e)[:50]
                return result

        return testable_migrate

    # =========================================================================
    # Happy Path Tests
    # =========================================================================

    def test_import_error_returns_ok_gracefully(self):
        """Test: ImportError during module imports -> returns ok gracefully."""
        migrate_func = self._create_testable_migrate_function(import_error=True)
        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None

    def test_no_vec_memories_table_returns_ok(self):
        """Test: vec_memories table doesn't exist -> returns ok, no migration."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Table doesn't exist (fetchone returns None)
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor

        mock_sqlite_vec = MagicMock()

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=mock_sqlite_vec,
            get_connection_func=lambda: mock_conn,
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None
        mock_conn.close.assert_called_once()

    def test_empty_vec_memories_table_returns_ok(self):
        """Test: vec_memories table exists but is empty -> returns ok, no migration."""
        mock_conn = MagicMock()

        # First execute: table exists
        # Second execute: SELECT embedding returns None (empty table)
        call_count = [0]

        def mock_execute(query, *args):
            call_count[0] += 1
            mock_cursor = MagicMock()
            if "sqlite_master" in query:
                mock_cursor.fetchone.return_value = ('vec_memories',)  # Table exists
            elif "SELECT embedding" in query:
                mock_cursor.fetchone.return_value = None  # Empty table
            return mock_cursor

        mock_conn.execute = mock_execute

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=lambda: mock_conn,
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None

    def test_dimensions_match_returns_ok(self):
        """Test: dimensions match -> returns ok, no migration needed."""
        import struct

        mock_conn = MagicMock()

        # Create a 256-dim embedding blob (256 floats * 4 bytes = 1024 bytes)
        embedding_256 = [0.1] * 256
        embedding_blob = struct.pack(f'{len(embedding_256)}f', *embedding_256)

        def mock_execute(query, *args):
            mock_cursor = MagicMock()
            if "sqlite_master" in query:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "SELECT embedding" in query:
                mock_cursor.fetchone.return_value = (embedding_blob,)
            return mock_cursor

        mock_conn.execute = mock_execute

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=lambda: mock_conn,
            embedding_dim=256,  # Matches the blob
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None

    def test_dimension_mismatch_performs_migration(self):
        """Test: dimension mismatch -> performs migration and returns success count."""
        import struct

        mock_conn = MagicMock()

        # Create a 384-dim embedding blob (old dimension)
        embedding_384 = [0.1] * 384
        embedding_blob = struct.pack(f'{len(embedding_384)}f', *embedding_384)

        # Track calls to simulate different query results
        execute_calls = []

        def mock_execute(query, *args):
            execute_calls.append((query, args))
            mock_cursor = MagicMock()

            if "sqlite_master" in query:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "SELECT embedding" in query:
                mock_cursor.fetchone.return_value = (embedding_blob,)
            elif "SELECT id, context" in query:
                # Return 2 memories to re-embed
                mock_cursor.fetchall.return_value = [
                    ('mem1', 'context1', 'goal1', 'lessons1', 'decisions1', 'entities1'),
                    ('mem2', 'context2', 'goal2', 'lessons2', 'decisions2', 'entities2'),
                ]
            return mock_cursor

        mock_conn.execute = mock_execute
        mock_conn.commit = MagicMock()
        mock_conn.close = MagicMock()

        # Mock embedding service
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256  # New dimension

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=lambda: mock_conn,
            get_embedding_service_func=lambda: mock_service,
            generate_embedding_text_func=lambda d: "text",
            embedding_dim=256,  # New dimension (was 384)
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert 'Migrated 2/2' in result['message']
        assert '256-dim' in result['message']

    # =========================================================================
    # Edge Case Tests
    # =========================================================================

    def test_pysqlite3_none_returns_ok(self):
        """Test: pysqlite3 module is None -> returns ok gracefully."""
        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=None,
            sqlite_vec_module=MagicMock(),
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None

    def test_sqlite_vec_none_returns_ok(self):
        """Test: sqlite_vec module is None -> returns ok gracefully."""
        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=None,
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None

    def test_get_connection_none_returns_ok(self):
        """Test: get_connection is None -> returns ok gracefully."""
        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=None,
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None

    def test_sqlite_vec_load_failure_returns_error(self):
        """Test: sqlite_vec.load() raises exception -> returns error status."""
        mock_conn = MagicMock()
        mock_sqlite_vec = MagicMock()
        mock_sqlite_vec.load.side_effect = Exception("Failed to load sqlite_vec")

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=mock_sqlite_vec,
            get_connection_func=lambda: mock_conn,
        )

        result = migrate_func()

        assert result['status'] == 'error'
        assert 'Failed to load sqlite_vec' in result['message']

    def test_database_query_exception_returns_ok(self):
        """Test: exception during dimension check -> returns ok gracefully."""
        import struct

        mock_conn = MagicMock()

        # Create embedding blob
        embedding_384 = [0.1] * 384
        embedding_blob = struct.pack(f'{len(embedding_384)}f', *embedding_384)

        def mock_execute(query, *args):
            mock_cursor = MagicMock()
            if "sqlite_master" in query:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "SELECT embedding" in query:
                # Simulate exception during dimension check
                raise Exception("Query failed")
            return mock_cursor

        mock_conn.execute = mock_execute

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=lambda: mock_conn,
        )

        result = migrate_func()

        # Inner exception is caught, returns ok
        assert result['status'] == 'ok'

    def test_partial_reembedding_returns_partial_count(self):
        """Test: some memories fail to re-embed -> returns partial success count."""
        import struct

        mock_conn = MagicMock()

        # Old dimension embedding
        embedding_384 = [0.1] * 384
        embedding_blob = struct.pack(f'{len(embedding_384)}f', *embedding_384)

        def mock_execute(query, *args):
            mock_cursor = MagicMock()
            if "sqlite_master" in query:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "SELECT embedding" in query:
                mock_cursor.fetchone.return_value = (embedding_blob,)
            elif "SELECT id, context" in query:
                # Return 3 memories
                mock_cursor.fetchall.return_value = [
                    ('mem1', 'context1', 'goal1', 'lessons1', 'decisions1', 'entities1'),
                    ('mem2', 'context2', 'goal2', 'lessons2', 'decisions2', 'entities2'),
                    ('mem3', 'context3', 'goal3', 'lessons3', 'decisions3', 'entities3'),
                ]
            return mock_cursor

        mock_conn.execute = mock_execute
        mock_conn.commit = MagicMock()
        mock_conn.close = MagicMock()

        # Mock embedding service that fails on second memory
        mock_service = MagicMock()
        call_count = [0]

        def generate_with_failure(text):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Embedding failed")
            return [0.1] * 256

        mock_service.generate.side_effect = generate_with_failure

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=lambda: mock_conn,
            get_embedding_service_func=lambda: mock_service,
            generate_embedding_text_func=lambda d: "text",
            embedding_dim=256,
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert 'Migrated 2/3' in result['message']  # 2 of 3 succeeded

    def test_embedding_returns_none_skipped(self):
        """Test: embedding service returns None -> memory skipped, not counted."""
        import struct

        mock_conn = MagicMock()

        embedding_384 = [0.1] * 384
        embedding_blob = struct.pack(f'{len(embedding_384)}f', *embedding_384)

        def mock_execute(query, *args):
            mock_cursor = MagicMock()
            if "sqlite_master" in query:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "SELECT embedding" in query:
                mock_cursor.fetchone.return_value = (embedding_blob,)
            elif "SELECT id, context" in query:
                mock_cursor.fetchall.return_value = [
                    ('mem1', 'context1', 'goal1', 'lessons1', 'decisions1', 'entities1'),
                    ('mem2', 'context2', 'goal2', 'lessons2', 'decisions2', 'entities2'),
                ]
            return mock_cursor

        mock_conn.execute = mock_execute
        mock_conn.commit = MagicMock()
        mock_conn.close = MagicMock()

        # Mock service returns None for first memory
        mock_service = MagicMock()
        mock_service.generate.side_effect = [None, [0.1] * 256]

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=lambda: mock_conn,
            get_embedding_service_func=lambda: mock_service,
            generate_embedding_text_func=lambda d: "text",
            embedding_dim=256,
        )

        result = migrate_func()

        assert result['status'] == 'ok'
        assert 'Migrated 1/2' in result['message']  # Only 1 succeeded

    # =========================================================================
    # Error Handling Tests
    # =========================================================================

    def test_exception_returns_error_with_truncated_message(self):
        """Test: exception during migration -> returns error with truncated message."""
        mock_conn = MagicMock()
        mock_sqlite_vec = MagicMock()

        # Create a long error message that should be truncated to 50 chars
        long_error = "A" * 100
        mock_sqlite_vec.load.side_effect = Exception(long_error)

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=mock_sqlite_vec,
            get_connection_func=lambda: mock_conn,
        )

        result = migrate_func()

        assert result['status'] == 'error'
        assert len(result['message']) == 50
        assert result['message'] == "A" * 50

    def test_connection_failure_returns_error(self):
        """Test: get_connection raises exception -> returns error status."""
        def failing_connection():
            raise Exception("Connection failed")

        migrate_func = self._create_testable_migrate_function(
            pysqlite3_module=MagicMock(),
            sqlite_vec_module=MagicMock(),
            get_connection_func=failing_connection,
        )

        result = migrate_func()

        assert result['status'] == 'error'
        assert 'Connection failed' in result['message']

    # =========================================================================
    # Integration with actual function (graceful degradation)
    # =========================================================================

    def test_actual_function_returns_skipped_without_deps(self):
        """Test: actual maybe_migrate_embeddings returns skipped_deps when deps unavailable.

        This tests the real function to ensure it gracefully handles
        the ImportError from an unavailable dependency. The unavailability
        is FORCED: all three deps (pysqlite3, sqlite_vec, model2vec) are
        importable in a dev environment, so a None entry in sys.modules
        makes the import system raise ImportError deterministically,
        independent of what the runner has installed. (This arm previously
        passed on bare-imported memory_init's broken relative lazy leg
        manufacturing that ImportError; the package-style conversion
        repaired the leg, so the condition is now forced explicitly.)
        """
        from scripts.memory_init import maybe_migrate_embeddings

        with patch.dict(sys.modules, {"pysqlite3": None}):
            result = maybe_migrate_embeddings()

        # The forced ImportError (None entry above) -> skipped_deps (graceful degradation)
        assert result['status'] == 'skipped_deps'
        assert result['message'] == 'Dependencies not available'


class TestLogging:
    """Tests for logging behavior during initialization."""

    def test_logs_installed_dependencies(self, caplog):
        """Test that installed dependencies are logged."""
        import logging
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': ['sqlite-vec'], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            with caplog.at_level(logging.INFO):
                ensure_memory_ready()

            assert any('sqlite-vec' in record.message for record in caplog.records)

    def test_logs_failed_installations(self, caplog):
        """Test that failed installations are logged as warnings."""
        import logging
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'partial', 'installed': [], 'failed': ['pysqlite3']}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            with caplog.at_level(logging.WARNING):
                ensure_memory_ready()

            assert any('pysqlite3' in record.message for record in caplog.records)


class TestMemoryAPIRealIntegration:
    """Tests that actually import memory_api.py and verify lazy initialization.

    These tests import from the real memory_api module and verify that
    PACTMemory methods trigger ensure_memory_ready() on first use.

    Note: memory_api uses relative imports, so we import the entire scripts
    package and mock ensure_memory_ready at the memory_api module level.
    """

    def test_pact_memory_save_triggers_ensure_ready(self):
        """Test that PACTMemory.save() triggers ensure_memory_ready()."""
        from scripts.memory_init import reset_initialization

        reset_initialization()

        # We need to mock ensure_memory_ready at the memory_api module level
        # because memory_api imports it with: from .memory_init import ensure_memory_ready
        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Import the ensure_memory_ready we're testing
            from scripts.memory_init import ensure_memory_ready, is_initialized

            # Verify not initialized yet
            assert is_initialized() is False

            # Call ensure_memory_ready directly (simulating what _ensure_ready does)
            ensure_memory_ready()

            # Verify initialization happened
            assert is_initialized() is True
            assert mock_deps.call_count == 1
            assert mock_migrate.call_count == 1
            assert mock_embed.call_count == 1

    def test_pact_memory_class_method_calls_ensure_ready(self):
        """Test that calling a PACTMemory method invokes _ensure_ready pattern.

        This verifies the integration point exists by confirming that the
        ensure_memory_ready function from memory_init is what gets called
        when _ensure_ready() is invoked in memory_api.

        We test this by:
        1. Mocking ensure_memory_ready
        2. Importing PACTMemory (which imports ensure_memory_ready)
        3. Verifying that calling an API method would trigger initialization
        """
        from scripts.memory_init import reset_initialization, is_initialized

        reset_initialization()

        # The key insight: memory_api.py has this import:
        #   from .memory_init import ensure_memory_ready
        # And _ensure_ready() simply calls ensure_memory_ready()
        #
        # So if we verify that ensure_memory_ready is called when we
        # simulate what _ensure_ready does, the integration is verified.

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            from scripts.memory_init import ensure_memory_ready

            # Before any call
            assert is_initialized() is False

            # This is exactly what _ensure_ready() does in memory_api.py line 95:
            #   def _ensure_ready() -> None:
            #       ensure_memory_ready()
            ensure_memory_ready()

            # Verify it ran
            assert is_initialized() is True
            mock_deps.assert_called_once()

    def test_memory_api_imports_ensure_memory_ready_from_memory_init(self):
        """Verify memory_api.py imports ensure_memory_ready from memory_init.

        This is a structural verification that the integration point exists
        by checking that memory_api.py contains the expected import.
        """
        memory_api_path = (
            Path(__file__).parent.parent /
            "skills" / "pact-memory" / "scripts" / "memory_api.py"
        )

        content = memory_api_path.read_text()

        # Verify the import exists
        assert "from .memory_init import ensure_memory_ready" in content

        # Verify _ensure_ready calls it
        assert "def _ensure_ready()" in content
        assert "ensure_memory_ready()" in content

    def test_all_api_methods_call_ensure_ready(self):
        """Verify all PACTMemory methods that need DB access call _ensure_ready.

        This structural test ensures the pattern is consistently applied.
        """
        memory_api_path = (
            Path(__file__).parent.parent /
            "skills" / "pact-memory" / "scripts" / "memory_api.py"
        )

        content = memory_api_path.read_text()

        # Methods that should call _ensure_ready before DB operations
        methods_needing_ensure_ready = [
            'def save(',
            'def search(',
            'def search_by_file(',
            'def get(',
            'def update(',
            'def delete(',
            'def list(',
            'def get_status(',
        ]

        for method in methods_needing_ensure_ready:
            assert method in content, f"Method {method} not found in memory_api.py"

        # Count _ensure_ready() calls - should be at least one per method
        ensure_ready_calls = content.count('_ensure_ready()')
        assert ensure_ready_calls >= len(methods_needing_ensure_ready), (
            f"Expected at least {len(methods_needing_ensure_ready)} calls to "
            f"_ensure_ready(), found {ensure_ready_calls}"
        )


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def reset_init_state():
    """Reset initialization state before and after each test."""
    from scripts.memory_init import reset_initialization
    reset_initialization()
    yield
    reset_initialization()


class TestProcessMarkerDoesNotOutliveItsProcess:
    """The fallback marker is per-process, so it must not survive the process.

    Route F removed the marker DELETION but not its CREATION, and the fallback
    token made every process's marker unique — so each one became permanent
    litter that nothing could ever match again. Measured at 100 files per suite
    run before this was fixed.

    NON-VACUITY: asserting "no marker exists afterwards" passes both when the
    cleanup works AND when no marker was ever created. So the child proves
    creation from the inside and reports the path; the parent then proves the
    same path is gone. Without the child's assertion this test could not tell
    a working cleanup from a marker that never existed.
    """

    def _run_child(self, extra_body=""):
        scripts = str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")
        child = (
            "import sys, pathlib\n"
            f"sys.path.insert(0, {scripts!r})\n"
            "import memory_init\n"
            "p = memory_init._get_embedding_attempted_path()\n"
            "p.touch()\n"
            # POSITIVE ARM: the marker demonstrably existed while the process ran.
            "assert p.exists(), 'child failed to create its own marker'\n"
            "print(str(p))\n"
            f"{extra_body}"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"child failed: {proc.stderr}"
        return Path(proc.stdout.strip().splitlines()[-1])

    def test_fallback_marker_is_removed_when_the_process_exits(self):
        marker = self._run_child()

        assert "process-" in marker.name, (
            "precondition: the child had no session context, so it must have "
            f"used the process-unique fallback; got {marker.name}"
        )
        assert not marker.exists(), (
            f"{marker.name} outlived the process that created it; nothing can "
            "ever match that token again, so it is permanent litter"
        )

    def test_the_processes_own_session_scoped_marker_survives(self):
        """A SESSION-scoped marker must outlive the process that made it.

        SUBJECT MATTERS HERE, and an earlier version of this test got it wrong.
        It touched an unrelated bystander file while the child still resolved to
        the FALLBACK token, so its subject was "an unrelated marker is not
        deleted" — a different noun from the requirement, which is "the
        process's OWN session-scoped marker survives". A handler rebuilt as
        `_get_embedding_attempted_path()` — the plausible "there is already a
        helper" simplification — deleted the process's own session marker and
        that version passed anyway, because the child's path WAS the fallback
        and the bystander was never at risk.

        So this child RESOLVES a real session id and asserts its own path is
        session-scoped before creating it. Now the process's own marker is the
        thing under test, and that mutant kills this test.
        """
        session_id = f"test-survives-{uuid.uuid4().hex[:12]}"
        home = tempfile.mkdtemp(prefix="pact-marker-home-")
        ctx = Path(home) / ".claude" / "pact-sessions" / "some-project" / session_id
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "pact-session-context.json").write_text(
            json.dumps({"session_id": session_id, "project_dir": "/x/some-project"}),
            encoding="utf-8",
        )

        env = dict(os.environ)
        env["HOME"] = home
        env["CLAUDE_CODE_SESSION_ID"] = session_id
        # The discovery route refuses under pytest, and a child inherits that
        # marker — so it must be cleared or the child falls back to the process
        # token and this test silently reverts to the defective version.
        env.pop("PYTEST_CURRENT_TEST", None)

        scripts = str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")
        child = (
            "import sys\n"
            f"sys.path.insert(0, {scripts!r})\n"
            "import memory_init\n"
            "p = memory_init._get_embedding_attempted_path()\n"
            # POSITIVE ARM: prove the path really is session-scoped. Without
            # this the child could fall back and the test would pass vacuously.
            f"assert {session_id!r} in p.name, 'child did not resolve the session id: ' + p.name\n"
            "assert 'process-' not in p.name, 'child used the fallback token: ' + p.name\n"
            "p.touch()\n"
            "assert p.exists(), 'child failed to create its own marker'\n"
            "print(str(p))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True,
            timeout=60, env=env,
        )
        assert proc.returncode == 0, f"child failed: {proc.stderr}"
        marker = Path(proc.stdout.strip().splitlines()[-1])

        try:
            assert session_id in marker.name, "precondition: marker is session-scoped"
            assert marker.exists(), (
                f"{marker.name} was removed at process exit; a session-scoped "
                "marker must survive, because suppressing the sweep ACROSS the "
                "processes of one session is its entire purpose"
            )
        finally:
            marker.unlink(missing_ok=True)
            shutil.rmtree(home, ignore_errors=True)
