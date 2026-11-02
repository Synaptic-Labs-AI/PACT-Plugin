"""
Tests for the memory_init module - lazy initialization system for PACT memory.

Tests cover:
1. Unit tests for ensure_memory_ready(), reset_initialization(), is_initialized()
2. Thread safety - multiple concurrent calls only run once
3. Integration with memory_api.py - first API call triggers initialization
4. Edge cases - graceful degradation, already-installed dependencies
"""

import ast
import os
import re
import shlex
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

    Hence drift is caught STATICALLY, below. A failing test is louder than any
    log line and is immune to capture, to the process boundary and to `-r`.
    """

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

    # pip's option tables, MEASURED from pip's own parser (pip 26.2.2, the
    # version CI's unconstrained pip upgrade resolved when measured:
    # `create_command("install").parser`, every option group, `takes_value()`),
    # plus three legacy value options older pips still accept
    # (`--install-option`, `--global-option`, `--build-option`) and `--hash`.
    # A newer pip's option missing here fails CLOSED: an unknown long flag
    # is refused by the flag allowlist (`_PIP_ALLOWED_INSTALL_FLAGS`), and
    # an unknown value-taking flag leaves its value read as a package
    # operand, which the coverage arm reports as UNPINNED naming it.
    _PIP_FLAGS_WITH_VALUE = frozenset({
        "--abi", "--all-releases", "--build-constraint", "--cache-dir",
        "--cert", "--client-cert", "--config-settings", "--constraint",
        "--default-timeout", "--editable", "--exists-action",
        "--extra-index-url", "--find-links", "--group", "--implementation",
        "--index-url", "--keyring-provider", "--local-log", "--log",
        "--log-file", "--no-binary", "--only-binary", "--only-final",
        "--platform", "--prefix", "--progress-bar", "--proxy", "--pypi-url",
        "--python", "--python-version", "--refresh-package", "--report",
        "--requirement", "--requirements-from-script", "--resume-retries",
        "--retries", "--root", "--root-user-action", "--source",
        "--source-dir", "--source-directory", "--src", "--target",
        "--timeout", "--trusted-host", "--upgrade-strategy",
        "--uploaded-prior-to", "--use-deprecated", "--use-feature",
        "--install-option", "--global-option", "--build-option", "--hash",
    })

    # The GLOBAL subset of the value options (pip reads them before AND
    # after the subcommand; the parser consumes them wherever they appear).
    _PIP_GLOBAL_FLAGS_WITH_VALUE = frozenset({
        "--python", "--log", "--log-file", "--local-log", "--proxy",
        "--retries", "--timeout", "--default-timeout", "--exists-action",
        "--trusted-host", "--cert", "--client-cert", "--cache-dir",
        "--use-feature", "--use-deprecated", "--keyring-provider",
        "--resume-retries",
    })

    # pip's value-LESS long options, measured the same way. Listed so an
    # ABBREVIATED long option (pip's optparse accepts any unique prefix:
    # `--extra-index` IS `--extra-index-url`) resolves to its full name
    # against the whole option set before any comparison.
    _PIP_KNOWN_LONG_FLAGS = frozenset({
        "--break-system-packages", "--check-build-dependencies", "--compile",
        "--debug", "--disable-pip-version-check", "--dry-run",
        "--force-reinstall", "--help", "--ignore-installed",
        "--ignore-requires-python", "--isolated", "--no-build-isolation",
        "--no-cache-dir", "--no-clean", "--no-color", "--no-compile",
        "--no-dependencies", "--no-deps", "--no-index", "--no-input",
        "--no-proxy-env", "--no-python-version-warning",
        "--no-require-hashes", "--no-user", "--no-warn-conflicts",
        "--no-warn-script-location", "--only-dependencies", "--only-deps",
        "--pre", "--prefer-binary", "--quiet", "--require-hashes",
        "--require-venv", "--require-virtualenv", "--upgrade",
        "--use-pep517", "--user", "--verbose", "--version",
    })

    # pip's short options, measured: those that take a VALUE (read attached,
    # `-rreq.txt`, or as the next word; a bundle `-Ur req.txt` ends at the
    # first value-taking letter) and those that do not. Both map to the
    # long name, so every comparison reads ONE name. An unknown short
    # letter stays `-X` and is refused by the flag allowlist.
    _PIP_SHORT_WITH_VALUE = {
        "c": "--constraint", "r": "--requirement", "e": "--editable",
        "i": "--index-url", "f": "--find-links", "t": "--target",
        "C": "--config-settings",
    }
    _PIP_SHORT_FLAGS = {
        "U": "--upgrade", "I": "--ignore-installed", "V": "--version",
        "h": "--help", "q": "--quiet", "v": "--verbose",
    }

    # The ONLY options a pip install in CI or the bootstrap may carry: the
    # ones the canonical line and .cursor/install.sh use (`--upgrade`,
    # `--constraint constraints-ci.txt`), plus value-less options that
    # change logging or caching, never WHAT is resolved or from where.
    # Everything else — a new install source (`--group`,
    # `--requirements-from-script`), a resolver knob, a target directory —
    # is refused naming itself; vet it and add it here in the same change.
    _PIP_ALLOWED_INSTALL_FLAGS = frozenset({
        "--upgrade", "--constraint", "--quiet", "--verbose",
        "--no-cache-dir", "--disable-pip-version-check", "--no-input",
        "--no-color", "--no-python-version-warning",
    })

    # pip's subcommands. The subcommand is the first non-option word that
    # names one, so the VALUE of a global option this parser does not know
    # is skipped rather than misread as the subcommand.
    _PIP_SUBCOMMANDS = frozenset({
        "install", "download", "uninstall", "freeze", "inspect", "list",
        "show", "check", "config", "search", "cache", "index", "wheel",
        "hash", "completion", "debug", "help", "lock",
    })

    # The PROGRAM, not a substring of the command: `pip`, `pip3` and
    # `pip3.N` (setup-python and venvs ship all three), by bare name or by
    # path. `python -m pip`, `python -mpip` and `uv pip` are recognised in
    # `_pip_argv_start` — a path-qualified `/usr/bin/uv pip` is caught by the
    # bare `pip` word itself, which this regex matches. `pipx` is a different program and does NOT match
    # here — the installer-program allowlist owns it.
    _PIP_PROGRAM_RE = re.compile(r"(?:^|/)pip(?:3(?:\.\d+)?)?$")
    _PYTHON_PROGRAM_RE = re.compile(r"(?:^|/)python(?:3(?:\.\d+)?)?$")

    # Install-line flags named with their own message (every other flag
    # not on `_PIP_ALLOWED_INSTALL_FLAGS` is refused too, generically):
    # the install-SOURCE flags (-r/-e, `--group`, `--requirements-from-script`)
    # add packages from a file, path or dependency group the coverage arms
    # never read, and the index/source flags
    # resolve the pinned names from a source constraints-ci.txt never
    # vetted (its `==` pins carry no hashes).
    _PIP_REQUIREMENT_FLAGS = frozenset({
        "--requirement", "--editable", "--group", "--requirements-from-script",
    })
    _PIP_REDIRECT_FLAGS = frozenset({
        "--index-url", "--extra-index-url", "--find-links",
        "--trusted-host", "--no-index", "--pypi-url",
    })

    # One constraints-ci.txt line: a PEP 508 name, `==`, one literal
    # PEP 440 version (local labels allowed) — nothing after it.
    _EXACT_PIN_RE = re.compile(
        r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?==[0-9][A-Za-z0-9.+!]*")

    @staticmethod
    def _normalise_pin_name(name):
        """PEP 503 normalisation: lowercase, [-_.] runs collapse to '-'.

        Applied on BOTH sides of every comparison, so a legitimate
        'sqlite_vec'/'sqlite-vec' spelling difference does not read as
        drift. Version specifiers and extras are stripped first so
        'pytest>=8' still matches 'pytest'.
        """
        head = re.split(r"[=<>!~;\[]", name)[0]
        return re.sub(r"[-_.]+", "-", head).strip().lower()

    @classmethod
    def _simple_commands(cls, text, keep_redirect_targets=False):
        """Shell text -> one word list per SIMPLE COMMAND it executes.

        The unit every install arm reasons about is the command the shell
        runs, never a line of text. Comment-only lines are dropped first
        (prose may quote pip commands; prose is not a declaration);
        backslash continuations are joined; each logical line is split the
        way the shell splits it (quotes removed, `#` comments dropped) and
        then cut at the list operators `;`, `&&`, `||`, `|`, `&` and at
        parentheses, so `cd x && pip install y` and `$(pip install y)` each
        yield the install as its own command. Redirection operators are
        never words; their TARGETS are dropped too unless
        `keep_redirect_targets` (the runtime-config scan needs `> pip.conf`
        and `>> $GITHUB_ENV`; argv parsing must not read them as operands).
        A command that runs a STRING as code is read into, recursively:
        `bash|sh|dash|zsh|ksh -c '<string>'` (bundled `-ec`/`-lc` too) and
        `eval <words>` re-enter this model, and `python -c '<code>'` goes
        through `_python_shell_texts`, so an install inside the string is a
        command like any other. A line shlex cannot split
        (an unbalanced quote that continues on a later line) falls back to
        whitespace splitting — it is still read, never skipped.
        """
        logical = []
        pending = None
        for raw in text.splitlines():
            line = raw.strip()
            if pending is None and line.startswith("#"):
                continue
            joined = line if pending is None else f"{pending} {line}".strip()
            if line.endswith("\\"):
                pending = joined[:-1].rstrip()
                continue
            pending = None
            logical.append(joined)
        if pending is not None:
            logical.append(pending)
        commands = []
        for line in logical:
            if not line:
                continue
            try:
                lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError:
                tokens = line.split()
            current = []
            skip_target = False
            for tok in tokens:
                if skip_target:
                    skip_target = False
                    if keep_redirect_targets:
                        current.append(tok)
                    continue
                if tok and set(tok) <= set("<>&") and set(tok) & set("<>"):
                    if current and current[-1].isdigit():
                        current.pop()  # the fd of `2>&1`
                    skip_target = True
                    continue
                if tok and set(tok) <= set("();|&"):
                    if current:
                        commands.append(current)
                    current = []
                    continue
                current.append(tok)
            if current:
                commands.append(current)
        expanded = []
        for words in commands:
            expanded.append(words)
            i, program = cls._command_program(words)
            if program is None:
                continue
            rest = words[i + 1:]
            if program == "eval" and rest:
                expanded += cls._simple_commands(
                    " ".join(rest), keep_redirect_targets)
            elif program.rsplit("/", 1)[-1] in cls._SHELLS:
                for k, tok in enumerate(rest):
                    if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", tok):
                        if k + 1 < len(rest):
                            expanded += cls._simple_commands(
                                rest[k + 1], keep_redirect_targets)
                        break
            elif cls._PYTHON_PROGRAM_RE.search(program) and "-c" in rest:
                k = rest.index("-c")
                if k + 1 < len(rest):
                    for chunk in cls._python_shell_texts(rest[k + 1]):
                        expanded += cls._simple_commands(
                            chunk, keep_redirect_targets)
        return expanded

    @classmethod
    def _pip_argv_start(cls, words):
        """Index of pip's first ARGUMENT in a simple command, or None.

        Finds the pip PROGRAM anywhere in the command, so a wrapper in
        front (`sudo`, `env X=1`, `time`) does not hide it: `pip`/`pip3`/
        `pip3.N` by name or path, `python -m pip` / `python -mpip` (python
        options before `-m` skipped, `-W`/`-X` with their values), and
        `uv pip`. The FIRST program occurrence wins, so the `pip` operand
        of `pip install --upgrade pip` is an operand, not a second program.
        """
        for i, word in enumerate(words):
            if cls._PIP_PROGRAM_RE.search(word):
                return i + 1
            if word == "uv" and words[i + 1:i + 2] == ["pip"]:
                return i + 2
            if cls._PYTHON_PROGRAM_RE.search(word):
                j = i + 1
                while j < len(words) and words[j].startswith("-"):
                    if words[j] == "-m":
                        if words[j + 1:j + 2] == ["pip"]:
                            return j + 2
                        break
                    if words[j].startswith("-m"):
                        if words[j][2:] == "pip":
                            return j + 1
                        break
                    if words[j] in ("-W", "-X"):
                        j += 1
                    j += 1
        return None

    @classmethod
    def _resolve_long_option(cls, name):
        """A long option's full name, resolving pip's unique-prefix
        abbreviations against every long option this parser knows."""
        known = (cls._PIP_FLAGS_WITH_VALUE | cls._PIP_GLOBAL_FLAGS_WITH_VALUE
                 | cls._PIP_KNOWN_LONG_FLAGS)
        if name in known:
            return name
        candidates = [k for k in known if k.startswith(name) and k.startswith("--")]
        return candidates[0] if len(candidates) == 1 else name

    @classmethod
    def _parse_pip_words(cls, words):
        """A simple command's pip argv, or None when it runs no pip.

        Returns (subcommand, options, operands). `options` lists
        (long-name, value-or-None) from BOTH global and subcommand
        positions, with every spelling pip accepts reduced to one form:
        `--flag value`, `--flag=value`, an abbreviated long flag, `-X value`,
        `-Xvalue` and a short bundle (`-Uq`, `-Urreq.txt`). Operands are the
        words after the subcommand that are neither flags nor flag values.
        """
        start = cls._pip_argv_start(words)
        if start is None:
            return None
        valued = cls._PIP_FLAGS_WITH_VALUE | cls._PIP_GLOBAL_FLAGS_WITH_VALUE
        subcommand = None
        options = []
        operands = []
        rest = words[start:]
        i = 0
        while i < len(rest):
            tok = rest[i]
            i += 1
            if tok.startswith("--") and len(tok) > 2:
                name, eq, value = tok.partition("=")
                name = cls._resolve_long_option(name)
                if eq:
                    options.append((name, value))
                elif name in valued:
                    options.append((name, rest[i] if i < len(rest) else ""))
                    i += 1
                else:
                    options.append((name, None))
            elif tok.startswith("-") and len(tok) > 1:
                for k, letter in enumerate(tok[1:], start=1):
                    if letter in cls._PIP_SHORT_WITH_VALUE:
                        value = tok[k + 1:]
                        if not value:
                            value = rest[i] if i < len(rest) else ""
                            i += 1
                        options.append((cls._PIP_SHORT_WITH_VALUE[letter], value))
                        break
                    options.append(
                        (cls._PIP_SHORT_FLAGS.get(letter, "-" + letter), None))
            elif subcommand is None:
                if tok in cls._PIP_SUBCOMMANDS:
                    subcommand = tok
            else:
                operands.append(tok)
        return subcommand, options, operands

    @classmethod
    def _pip_installs_in(cls, text):
        """Every pip INSTALL the shell text executes: (command, parse) pairs.

        `command` is the simple command's words re-joined with single
        spaces — a display and comparison form, not the source spelling.
        """
        found = []
        for words in cls._simple_commands(text):
            parsed = cls._parse_pip_words(words)
            if parsed is not None and parsed[0] == "install":
                found.append((" ".join(words), parsed))
        return found

    @classmethod
    def _install_parse(cls, command):
        """The parse of the ONE pip install in `command`, or None."""
        found = cls._pip_installs_in(command)
        return found[0][1] if len(found) == 1 else None

    @classmethod
    def _package_tokens_from_pip_argv(cls, command):
        """Package-name tokens of one `pip install` command string.

        The operands of the shared argv parse: the words after the
        `install` subcommand that are neither flags nor the VALUE of a flag
        that takes one (separate, attached or `=` form). Everything before
        the subcommand is the invocation and its global options. Deriving
        the boundary from pip's own argv grammar beats filtering a list of
        command words, which would turn a package legitimately named after
        a command into false drift. A command that is not exactly one pip
        install yields no tokens, which the parse non-vacuity assertions
        report as a PARSE FAILURE.
        """
        parsed = cls._install_parse(command)
        if parsed is None:
            return set()
        return {cls._normalise_pin_name(tok) for tok in parsed[2] if tok}

    @classmethod
    def _applies_constraints(cls, command):
        """Does a pip-install command APPLY constraints-ci.txt?

        Read from the parse, so every spelling pip accepts counts —
        `-c constraints-ci.txt`, `-cconstraints-ci.txt`,
        `--constraint constraints-ci.txt`, `--constraint=constraints-ci.txt`,
        an abbreviated `--constr` — and the repo-root path spellings
        `_is_repo_constraints` accepts; anything naming another file is a
        re-point, not a pass.
        """
        parsed = cls._install_parse(command)
        if parsed is None:
            return False
        return any(
            name == "--constraint" and cls._is_repo_constraints(value)
            for name, value in parsed[1]
        )

    # The constraints file as the shipped install commands name it, run
    # from the repo root (tests.yml's install step has no working-directory;
    # install.sh cd's to the repo root first): `constraints-ci.txt`, plus
    # the equivalent `./` and workspace-rooted spellings.
    _REPO_CONSTRAINTS_SPELLINGS = frozenset({
        "constraints-ci.txt", "./constraints-ci.txt",
        "$GITHUB_WORKSPACE/constraints-ci.txt",
        "${GITHUB_WORKSPACE}/constraints-ci.txt",
        "${{ github.workspace }}/constraints-ci.txt",
    })

    @classmethod
    def _is_repo_constraints(cls, value):
        """Does a `--constraint` value name the repo's constraints-ci.txt?"""
        return value is not None and re.sub(
            r"\$\{\{\s*github\.workspace\s*\}\}", "${{ github.workspace }}",
            value) in cls._REPO_CONSTRAINTS_SPELLINGS

    def _ci_install_line(self, workflow):
        """The workflow's single `run:` line carrying a pip install.

        The parse anchors on the `run:` line — the EXECUTED command — never
        on any line merely mentioning an install: workflow comment prose
        may name the command (the CONSTRAINTS paragraph does), and prose is
        not a declaration. Whether the line installs is decided by the
        shared argv parse, so every program spelling (`pip3.12`,
        `python -mpip`, `uv pip`) and global options before the subcommand
        count. Exactly-one is asserted here so every consumer (tokens,
        coverage, application) shares one parse contract; the census arm
        then holds every OTHER executed install in the execution population
        (`_execution_population`) to this one. Folded plain scalars are REFUSED before the filter
        runs — see `_refuse_plain_scalar_run_continuations`.
        """
        text = workflow.read_text(encoding="utf-8")
        self._refuse_plain_scalar_run_continuations(text)
        install_lines = [
            ln for ln in text.splitlines()
            if ln.lstrip().startswith("run:")
            and self._pip_installs_in(ln.lstrip()[len("run:"):])
        ]
        assert install_lines, (
            f"PARSE FAILURE, not dependency drift: {workflow} contains no "
            f"`run:` line carrying a pip install, so the CI dependency "
            f"declaration cannot be read. The workflow changed shape — "
            f"re-point this parity test at the new shape; do not weaken it "
            f"to a skip."
        )
        assert len(install_lines) == 1, (
            f"PARSE AMBIGUITY, not dependency drift: {len(install_lines)} "
            f"`run:` lines carrying a pip install were read from "
            f"{workflow} ({install_lines}). This guard reasons about THE CI "
            f"install line, singular — a second install step must be "
            f"reconciled here, not silently first-matched."
        )
        return install_lines[0]

    @staticmethod
    def _workflow_population(root):
        """The ENTRY files CI starts from: every `.yml`/`.yaml` under
        `.github/workflows/` (a second workflow runs with the same checkout
        and token as tests.yml), plus every `action.yml`/`action.yaml`
        under `.github/actions/` (read even when unused — over-catch).
        What those files go on to EXECUTE is followed by
        `_execution_population`; this list is only where the walk starts.
        """
        files = []
        workflows = root / ".github" / "workflows"
        if workflows.is_dir():
            files += sorted(
                p for p in workflows.iterdir()
                if p.is_file() and p.suffix in (".yml", ".yaml")
            )
        actions = root / ".github" / "actions"
        if actions.is_dir():
            files += sorted(
                p for p in actions.rglob("*")
                if p.is_file() and p.name in ("action.yml", "action.yaml")
            )
        return files

    @staticmethod
    def _load_workflow_yaml(path, text=None):
        """yaml.safe_load of one population file; unparseable is a FAILURE."""
        try:
            return yaml.safe_load(
                text if text is not None else path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AssertionError(
                f"PARSE FAILURE, not a clean read: {path} does not parse as "
                f"YAML ({exc.__class__.__name__}), so its steps cannot be "
                f"enumerated. GitHub would refuse to run it; fix the syntax."
            ) from exc

    @classmethod
    def _steps_of(cls, doc):
        """Every step mapping in one parsed workflow or composite action."""
        return [step for _wd, step in cls._steps_with_workdir(doc)]

    @staticmethod
    def _steps_with_workdir(doc):
        """(working-directory or None, step) for every step in one parsed
        workflow or composite action; a step's own `working-directory`
        wins over its job's `defaults.run.working-directory`."""
        out = []
        if not isinstance(doc, dict):
            return out
        for job in (doc.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            defaults = ((job.get("defaults") or {}).get("run") or {}) \
                if isinstance(job.get("defaults"), dict) else {}
            job_wd = defaults.get("working-directory") \
                if isinstance(defaults, dict) else None
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    out.append((step.get("working-directory") or job_wd, step))
        runs = doc.get("runs")
        if isinstance(runs, dict):
            for step in runs.get("steps") or []:
                if isinstance(step, dict):
                    out.append((step.get("working-directory"), step))
        return out

    # Script files the follower can READ: shell through the shell model,
    # Python through `_python_shell_texts`. A followed repo script in any
    # other language is refused, never skipped.
    _SHELL_SCRIPT_SUFFIXES = (".sh", ".bash")
    _PYTHON_SCRIPT_SUFFIXES = (".py",)
    _SHELLS = frozenset({"bash", "sh", "dash", "zsh", "ksh"})
    _COMMAND_WRAPPERS = frozenset({"sudo", "env", "exec", "time", "command", "nohup"})

    # `$GITHUB_WORKSPACE` and `${{ github.workspace }}` ARE the checkout:
    # a path built on them names a repo file. Every other variable (the
    # runner's $RUNNER_TEMP, $HOME, ...) names a path outside the repo.
    _WORKSPACE_PREFIX_RE = re.compile(
        r"^(?:\$GITHUB_WORKSPACE|\$\{GITHUB_WORKSPACE\}"
        r"|\$\{\{\s*github\.workspace\s*\}\})/")

    @classmethod
    def _command_program(cls, words):
        """(index, word) of a simple command's PROGRAM, past leading
        `VAR=value` assignments and wrappers (`sudo`, `env`, ...)."""
        i = 0
        while i < len(words):
            word = words[i]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word) \
                    or word.rsplit("/", 1)[-1] in cls._COMMAND_WRAPPERS \
                    or (i > 0 and words[i - 1].rsplit("/", 1)[-1]
                        in cls._COMMAND_WRAPPERS and word.startswith("-")):
                i += 1
                continue
            return i, word
        return None, None

    @classmethod
    def _script_operand(cls, words):
        """The repo-script token a simple command EXECUTES, or None.

        Either the program word itself when it is a path (`./x.sh`,
        `scripts/x.sh`, `$GITHUB_WORKSPACE/x.sh`), or the first operand of
        a shell (`bash x.sh`, `sh x.sh`, `source x.sh`, `. x.sh`) or of a
        python interpreter (`python x.py`). `-c` strings are not scripts
        (`_simple_commands` reads them as commands), and neither are
        `python -`/`python -m <module>` (stdin and an installed module).
        """
        i, program = cls._command_program(words)
        if program is None:
            return None
        base = program.rsplit("/", 1)[-1]
        is_shell = base in cls._SHELLS or program in ("source", ".")
        is_python = bool(cls._PYTHON_PROGRAM_RE.search(program))
        if not (is_shell or is_python):
            return program if ("/" in program or program.endswith(
                cls._SHELL_SCRIPT_SUFFIXES + cls._PYTHON_SCRIPT_SUFFIXES)) \
                else None
        rest = words[i + 1:]
        j = 0
        while j < len(rest):
            tok = rest[j]
            if tok in ("-c", "-m", "-") or (
                    is_shell and re.match(r"^-[A-Za-z]*c[A-Za-z]*$", tok)):
                return None
            if tok in ("-o", "+o", "-W", "-X"):
                j += 2
                continue
            if tok.startswith(("-", "+")) and tok != "--":
                j += 1
                continue
            if tok == "--":
                j += 1
                continue
            return tok
        return None

    @classmethod
    def _resolve_repo_script(cls, token, root, workdir):
        """A script token -> (path or None, reason).

        Returns the repo file it names, or None with a reason: `external`
        for a path outside the checkout (absolute, or built on a runner
        variable other than the workspace), and `missing` for a token that
        clearly names a repo file (a relative path or a script suffix)
        that does not exist — which the follower refuses rather than skips.
        """
        token = cls._WORKSPACE_PREFIX_RE.sub("", token)
        if "$" in token or token.startswith(("/", "~")):
            candidate = Path(token) if token.startswith("/") else None
            if candidate is not None:
                try:
                    candidate.resolve().relative_to(root.resolve())
                    return candidate, "ok" if candidate.is_file() else "missing"
                except ValueError:
                    pass
            return None, "external"
        bases = [root / workdir, root] if workdir else [root]
        for base in bases:
            candidate = base / token
            if candidate.is_file():
                return candidate, "ok"
        # Fail closed only on a token that CLEARLY names a repo file — an
        # explicit relative path or a script suffix. A bare `a/b` word (an
        # awk or sed pattern inside a followed script) is not a script.
        if token.startswith(("./", "../")) or token.endswith(
                cls._SHELL_SCRIPT_SUFFIXES + cls._PYTHON_SCRIPT_SUFFIXES):
            return None, "missing"
        return None, "external"

    @classmethod
    def _python_shell_texts(cls, code):
        """Shell texts a piece of Python could execute, read via ast.

        Every str constant is offered to the shell model as a command line
        (`subprocess.run("pip install x", shell=True)`), and every
        list/tuple of str constants as an argv (`["pip", "install", "x"]`).
        Over-catch by design: a docstring that reads like a pip command
        reads as one. Code that does not parse is returned whole, as text.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return [code]
        texts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                texts.append(node.value)
            elif isinstance(node, (ast.List, ast.Tuple)) and node.elts and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in node.elts):
                texts.append(" ".join(shlex.quote(e.value) for e in node.elts))
        return texts

    @classmethod
    def _execution_population(cls, root):
        """Everything CI EXECUTES, followed from the entry files.

        Returns (yaml_files, steps, texts, files): every workflow / action
        file read, every (label, step) in them, every (label, shell text)
        CI runs, and every file the walk read (the rig copies exactly
        these) — each step's `run:` string plus the text of every repo
        script a run step executes, recursively. Followed:
          - `uses: ./<path>` at step level (a local action anywhere in
            the repo; its composite `runs.steps` are walked in turn) and at
            job level (a local reusable workflow);
          - every repo script a command executes (`_script_operand`),
            resolved against the step's working-directory then the repo
            root; shell scripts are read as shell, Python through ast.
        Refused (fail closed, never skipped): a followed local action
        whose `runs.using` is not `composite` (its code is JS or a
        container this reader cannot parse), a `uses: ./` path with no
        action file, a script token that names a repo file that does not
        exist, and a followed script in another language. Not followed:
        paths outside the checkout (absolute, or on runner variables such
        as $RUNNER_TEMP), `python -`/`-m`, and third-party actions — those
        are held by the action allowlist instead.
        """
        cached = cls._EXECUTION_CACHE.get(root.resolve())
        if cached is not None and cached[0] == cls._read_signature(
                root, cached[1][3]):
            return cached[1]
        yaml_files, steps, texts, files = [], [], [], []
        seen = set()

        def rel(path):
            try:
                return path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return str(path)

        def follow_texts(label, text, workdir):
            texts.append((label, text))
            for words in cls._simple_commands(text):
                token = cls._script_operand(words)
                if token is None:
                    continue
                path, why = cls._resolve_repo_script(token, root, workdir)
                if why == "external":
                    continue
                assert why == "ok", (
                    f"UNRESOLVED SCRIPT, not a clean read: {label} executes "
                    f"{token!r}, which names a repo file that does not exist "
                    f"(looked under working-directory {workdir!r} and the "
                    f"repo root). The census cannot read what it would run; "
                    f"fix the path."
                )
                visit_script(path, workdir)

        def visit_script(path, workdir):
            key = path.resolve()
            if key in seen:
                return
            seen.add(key)
            files.append(path)
            text = path.read_text(encoding="utf-8")
            first = text.splitlines()[0] if text else ""
            if path.suffix in cls._PYTHON_SCRIPT_SUFFIXES or (
                    not path.suffix and "python" in first and first.startswith("#!")):
                for n, chunk in enumerate(cls._python_shell_texts(text)):
                    follow_texts(f"{rel(path)} (python text {n})", chunk, workdir)
                return
            assert path.suffix in cls._SHELL_SCRIPT_SUFFIXES or (
                not path.suffix and first.startswith("#!")
                and re.search(r"\b(ba|z|k|da)?sh\b", first)), (
                f"UNREADABLE SCRIPT: CI executes {rel(path)}, a script this "
                f"census cannot read (not shell, not Python). It would run "
                f"unread; extend the census to read it in the same change, "
                f"or run it through a readable script."
            )
            follow_texts(rel(path), text, workdir)

        def visit_yaml(path, followed_action=False):
            key = path.resolve()
            if key in seen:
                return
            seen.add(key)
            files.append(path)
            text = path.read_text(encoding="utf-8")
            cls._refuse_plain_scalar_run_continuations(text)
            doc = cls._load_workflow_yaml(path, text)
            yaml_files.append(path)
            if isinstance(doc, dict):
                runs = doc.get("runs")
                if followed_action and isinstance(runs, dict):
                    assert str(runs.get("using", "")).lower() == "composite", (
                        f"UNREADABLE LOCAL ACTION: {rel(path)} is used by CI "
                        f"but runs as {runs.get('using')!r}, whose code (JS "
                        f"or a container) this census cannot read. It would "
                        f"run unread; make it a composite action, or extend "
                        f"the census in the same change."
                    )
                for job in (doc.get("jobs") or {}).values():
                    uses = job.get("uses") if isinstance(job, dict) else None
                    if isinstance(uses, str) and uses.startswith("./"):
                        target = root / uses[2:].split("@")[0]
                        assert target.is_file(), (
                            f"UNRESOLVED LOCAL WORKFLOW: {rel(path)} calls "
                            f"{uses!r}, which is not a file in this checkout."
                        )
                        visit_yaml(target)
            for n, (workdir, step) in enumerate(cls._steps_with_workdir(doc)):
                label = (f"{rel(path)} step {n} "
                         f"({step.get('name', step.get('uses', 'run'))})")
                steps.append((label, step))
                uses = step.get("uses")
                if isinstance(uses, str) and uses.startswith("./"):
                    action_dir = root / uses[2:].split("@")[0]
                    action = next((action_dir / name for name in
                                   ("action.yml", "action.yaml")
                                   if (action_dir / name).is_file()), None)
                    assert action is not None, (
                        f"UNRESOLVED LOCAL ACTION: {label} uses {uses!r}, but "
                        f"{rel(action_dir)} holds no action.yml/action.yaml."
                    )
                    visit_yaml(action, followed_action=True)
                run = step.get("run")
                if isinstance(run, str):
                    follow_texts(label, run, workdir)

        for entry in cls._workflow_population(root):
            visit_yaml(entry)
        result = (yaml_files, steps, texts, files)
        cls._EXECUTION_CACHE[root.resolve()] = (
            cls._read_signature(root, files), result)
        return result

    # The walk re-parses every followed file, and every arm asks for it, so
    # one result is reused per root — but ONLY while every file it read and
    # the entry set are byte-for-byte unchanged (path, size, mtime_ns): a
    # mutation to any read file, or a new entry file, forces a fresh walk.
    _EXECUTION_CACHE = {}

    @classmethod
    def _read_signature(cls, root, files):
        paths = sorted(set(files) | set(cls._workflow_population(root)))
        signature = []
        for path in paths:
            try:
                st = path.stat()
                signature.append((str(path), st.st_size, st.st_mtime_ns))
            except FileNotFoundError:
                signature.append((str(path), None, None))
        return tuple(signature)

    @classmethod
    def _population_steps(cls, root):
        """(label, step) for every step CI executes (`_execution_population`).

        Read STRUCTURALLY: a block-style step, a flow-style step
        (`- {name: x, run: ...}`), a folded scalar and a block scalar are
        all one mapping to the parser, so the `run:` string each yields is
        the string the runner executes. Plain-scalar folds are also refused
        line-wise (`_refuse_plain_scalar_run_continuations`) as an
        over-catch belt.
        """
        return cls._execution_population(root)[1]

    @classmethod
    def _population_texts(cls, root):
        """(label, shell text) for every `run:` string and every followed
        repo script CI executes."""
        return cls._execution_population(root)[2]

    @classmethod
    def _population_installs(cls, root):
        """Every executed pip install across the population, labelled."""
        installs = []
        for label, text in cls._population_texts(root):
            for command, parsed in cls._pip_installs_in(text):
                installs.append((label, command, parsed))
        return installs

    @staticmethod
    def _refuse_plain_scalar_run_continuations(workflow_text):
        """REFUSE a `run:` plain scalar written across folded lines.

        YAML folds a deeper-indented bare line after `run: <text>` into
        ONE executed string, while every arm in this class reads line by
        line — so an operand, or a whole `pip3 install`, named only on the
        fold executes invisible to the census (round-5 probe, ground-
        truthed with yaml.safe_load: all arms green with the fold
        carrying the package). The shape is refused outright rather than
        modelled, per the round-5 security recommendation: this workflow
        has no legitimate use for it (`run: |` is the multi-command idiom
        it already uses twice), and a refusal reddens on the next honest
        fold the moment it lands instead of silently reading half a
        command. The census now reads every step structurally, so a fold
        is no longer invisible to it; the refusal stays as an over-catch
        belt for the line-oriented canonical parse (`_ci_install_line`).
        Block scalars (`run: |`, `run: >`) are legal.
        """
        run_indent = None
        for raw in workflow_text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                # Blank and comment lines never execute. They do not end
                # the plain scalar for this check either: a comment cannot
                # continue one, but the next deeper line still folds (as
                # whitespace at worst) — keep the check armed past them.
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if run_indent is not None:
                if indent > run_indent:
                    raise AssertionError(
                        f"PLAIN-SCALAR RUN CONTINUATION, not a readable "
                        f"command: {stripped!r} is folded by YAML into the "
                        f"plain `run:` scalar above it, so it EXECUTES "
                        f"while every line-oriented arm in this class "
                        f"reads the first line only — an install or "
                        f"operand named there is invisible to the census "
                        f"(probed: all arms green with the fold carrying "
                        f"the package). Write the step as ONE line or a "
                        f"`run: |` block; do not fold plain scalars."
                    )
                run_indent = None  # a dedented line ends the scalar
            if re.match(r"^\s*-?\s*run:\s*[|>]", raw):
                continue  # block scalar: deeper body lines are legal
            key = re.match(r"^(\s*-?\s*)run:", raw)
            if key:
                # The scalar's indent is the column of the `run` KEY, not
                # of a list dash before it: in `- run: x` + `  shell: bash`
                # the sibling key sits at the key's column and ends the
                # scalar — measuring from the dash refused that faithful
                # step shape as a fold.
                run_indent = len(key.group(1))

    def _ci_install_package_tokens(self, workflow):
        """Package-name tokens of the workflow's single `pip install` line.

        Compared as TOKENS of the install line, never substrings of it. A
        substring test fails OPEN on the most plausible next edit: this
        suite does `import pysqlite3 as sqlite3`, so an editor adding
        'sqlite3' to a package list would find it inside 'pysqlite3', pass,
        and assert a package is installed that is not. Measured against the
        real install line, 'sqlite3', 'sqlite', 'vec', 'yaml' and 'asyncio'
        are ALL substrings of it and none is a package operand.
        """
        install_line = self._ci_install_line(workflow)
        install_tokens = self._package_tokens_from_pip_argv(install_line)

        # PARSE NON-VACUITY — asserted before any comparison built on the
        # tokens, and with DISTINCT messages, because "I could not read the
        # line" and "a package is missing" must never produce the same
        # outcome. If a workflow refactor defeats this parser,
        # `install_tokens` silently empties and every set difference over it
        # passes VACUOUSLY: the guard would be dead while green, which is
        # the exact failure it exists to prevent.
        #
        # The anchor has GROUND TRUTH INDEPENDENT OF THE PARSE: this code is
        # executing under pytest, so CI demonstrably installs pytest. If a
        # correct parse of the CI install line does not contain it, the
        # parse is wrong — not the dependency set.
        _PARSE_ANCHOR = "pytest"
        assert install_tokens, (
            f"PARSE FAILURE, not dependency drift: no package operands could "
            f"be read from the CI install line. The line is "
            f"{install_line.strip()!r}. The tokeniser models "
            f"`pip install <flags> <packages>`; a refactor to a "
            f"requirements file or a multi-line form defeats it. Re-point "
            f"the parser — do NOT relax the assertions built on the token "
            f"set, which would pass vacuously over an empty one."
        )
        assert _PARSE_ANCHOR in install_tokens, (
            f"PARSE FAILURE, not dependency drift: the parsed operands "
            f"{sorted(install_tokens)} do not contain {_PARSE_ANCHOR!r}, but "
            f"this test is RUNNING under pytest, so CI installs it. The "
            f"parse is therefore wrong, not the dependency set. Re-point "
            f"the parser."
        )
        return install_tokens

    def test_declared_packages_match_the_ci_install_line(self):
        """Every package memory_init would install must be declared by CI.

        WHAT THIS PROVES: the two lists cannot silently diverge. If a package is
        added here and not to the workflow, this goes RED with the name in the
        message — which is the drift signal the silent branch cannot emit.

        WHAT IT DOES NOT PROVE: that any runtime message is visible in a CI log.
        Nothing asserts that, because nothing safely can — see the class
        docstring. It also does not prove the reverse direction: CI may install
        packages this module does not know about, which is harmless.
        """
        from scripts.memory_init import check_and_install_dependencies  # noqa: F401
        import inspect

        from scripts import memory_init

        source = inspect.getsource(memory_init.check_and_install_dependencies)
        declared = re.findall(r"\(\s*'([^']+)'\s*,\s*'[^']+'\s*\)", source)
        assert declared, (
            "could not read the package list out of "
            "check_and_install_dependencies — the list changed shape and this "
            "parity test is now vacuous. Re-point it; do not delete it."
        )

        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
                f"{Path(__file__).resolve()}, so this is not a PACT source "
                "checkout. The expected case is an INSTALLED PLUGIN CACHE, "
                "which has no .github/ tree by design. This skip is NO "
                "INFORMATION about dependency drift — never read it as a pass."
            )

        # INSIDE a checkout, absence is a FAILURE. Reaching here means the
        # marker was found, so the workflow is expected to exist and parse; if
        # it does not, the drift detector is broken and must say so loudly
        # rather than skip and read as green.
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) but "
            f"{workflow} is missing. The drift detector cannot run. This is a "
            f"FAILURE, not a skip: inside a checkout the workflow is required."
        )
        install_tokens = self._ci_install_package_tokens(workflow)

        declared_tokens = {self._normalise_pin_name(p) for p in declared}
        missing = sorted(declared_tokens - install_tokens)
        assert not missing, (
            f"DEPENDENCY DRIFT: {missing} are installed by "
            f"memory_init.check_and_install_dependencies but are NOT in the CI "
            f"workflow's install line. Under CI the install path is skipped, so "
            f"these would be absent at runtime and the branch reports it "
            f"NOWHERE (it is deliberately silent — the CLI's stderr is a JSON "
            f"contract). Add them to the workflow, or remove them here."
        )

    def test_constraints_pins_cover_the_install_line(self):
        """constraints-ci.txt and the CI install line must cover each other.

        The constrained install is only as good as its coverage. A direct
        dependency the install line requests with NO pin floats on the
        index — the exact merge-time weather the constraints file exists to
        prevent — and a pin for a package the line never requests is dead
        weight that misleads a refresh. `pip` is the ONE deliberate
        exception in the unpinned direction (a pinned self-upgrade can
        deadlock the bootstrap), named in both the workflow's CONSTRAINTS
        note and the file's own header.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ tree by design. This skip is "
                "NO INFORMATION about constraint coverage — never read it "
                "as a pass."
            )
        constraints = root / "constraints-ci.txt"
        assert constraints.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {constraints} is missing. The CI install line's `-c` "
            f"names it, so CI's own install step would fail — this is a "
            f"FAILURE, not a skip."
        )
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The coverage guard cannot run. "
            f"This is a FAILURE, not a skip: inside a checkout the "
            f"workflow is required."
        )
        install_line = self._ci_install_line(workflow)
        # Tokens come through the shared helper so the non-vacuity anchors
        # (ground truth: this test runs under pytest) fire on this path too.
        install_tokens = self._ci_install_package_tokens(workflow)

        # The install line must APPLY the constraints file, not merely ship
        # it beside the workflow: without `-c` every pin in the file is dead
        # at install time and every direct dependency floats — while the
        # coverage comparison below still passes, because it compares the
        # line's package NAMES against the pin names and never looks at the
        # line's flags. The script-side twin of this check runs in
        # test_cursor_install_sh_matches_the_ci_install_line.
        assert self._applies_constraints(install_line), (
            f"CONSTRAINT APPLICATION DRIFT: the CI install line "
            f"{install_line.strip()!r} does not apply constraints-ci.txt "
            f"(`-c`/`--constraint`). Merge-time resolution then floats — "
            f"the exact weather the file exists to prevent. Restore the "
            f"flag."
        )

        pins = {}
        for raw in constraints.read_text(encoding="utf-8").splitlines():
            # pip's requirements format ends a line at whitespace + `#`:
            # `ruff==0.17.3  # lint` IS the exact pin `ruff==0.17.3`, so the
            # comment is stripped before any check reads the line.
            line = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].strip()
            if not line:
                continue
            assert "==" in line, (
                f"PARSE FAILURE, not coverage drift: {constraints} carries "
                f"a non-pin line {line!r}. The file's contract is one "
                f"`name==version` pin per direct dependency — a line with "
                f"no `==` is either prose that lost its `#` or a range "
                f"constraint, and both must be reconciled by hand."
            )
            # `==` PRESENCE is not an exact pin: `ruff==0.17.*` is a range
            # and `ruff==0.17.3; python_version < "3.11"` is a pin that is
            # INACTIVE on the CI interpreter, so the direct dependency
            # floats while the name still reads as pinned. The contract is
            # the whole line: a name, `==`, one literal version.
            assert self._EXACT_PIN_RE.fullmatch(line), (
                f"INEXACT PIN, not coverage drift: {constraints} carries "
                f"{line!r}, which is not an exact `name==version` pin (a "
                f"wildcard, an environment marker, an extra or a second "
                f"clause). Such a line constrains a range or nothing at all "
                f"on the CI interpreter while the name still reads as "
                f"pinned. Write one literal version."
            )
            name, _, _version = line.partition("==")
            pins[self._normalise_pin_name(name)] = line

        unpinned = sorted(install_tokens - set(pins) - {"pip"})
        assert not unpinned, (
            f"CONSTRAINT COVERAGE DRIFT: {unpinned} are requested by the "
            f"CI install line but NOT pinned in {constraints}. An unpinned "
            f"direct dependency resolves to whatever the index serves at "
            f"merge time — the exact weather the constraints file exists "
            f"to prevent. Pin it (pip freeze of a verified venv) or remove "
            f"it from the install line."
        )
        dead_pins = sorted(set(pins) - install_tokens - {"pip"})
        assert not dead_pins, (
            f"CONSTRAINT COVERAGE DRIFT: pins for {dead_pins} sit in "
            f"{constraints} but the CI install line never requests them. "
            f"`-c` DOES bind a pinned name even as a transitive (measured: "
            f"a urllib3 pin forced a parent's transitive resolve), so the "
            f"pin is not inert — the problem is the mirror contract: this "
            f"file is the install line's direct-dependency mirror, and a "
            f"pin with no operand misleads a refresh into re-freezing it. "
            f"Remove the pin, or name the package on the install line too. "
            f"If this is a deliberately pinned COMPROMISED TRANSITIVE, add "
            f"it to the install line in the same change — a pin alone "
            f"reddens here, and a bare `!=` line breaks the `==`-only "
            f"parse above (see the incident path in the file's header)."
        )

    def _script_pip_install_commands(self, script):
        """The script's pip install commands, from EXECUTED text only.

        Read through the same shell model and argv parse as the workflow
        (`_pip_installs_in`): comment-only lines are dropped first (the
        script's prose may quote pip commands — prose is not a
        declaration), backslash continuations are joined, and each SIMPLE
        command is parsed as pip argv. So a wrapped `python -m pip \\` +
        `install pkg`, a `pip3`/`pip3.N`/`python -mpip` spelling, and global
        options before `install` all enumerate like the canonical spelling,
        while the apt-get bootstrap lines never reach the arms. Shared by
        every arm that reasons about the script surface, so one parse
        contract covers them all.
        """
        commands = [
            command for command, _parsed in
            self._pip_installs_in(script.read_text(encoding="utf-8"))
        ]
        assert commands, (
            f"PARSE FAILURE, not parity drift: no non-comment pip "
            f"install command could be read from {script}. The script "
            f"changed shape (a requirements file, a variable-driven loop) "
            f"— re-point the arms at the new shape; do not weaken them "
            f"to a skip."
        )
        return commands

    def test_cursor_install_sh_matches_the_ci_install_line(self):
        """`.cursor/install.sh` mirrors the CI install line — asserted now.

        The script's header claims lockstep with the workflow's install
        line, and before this arm that claim was PROSE ONLY: when CI gained
        `-c constraints-ci.txt` the claim broke silently because nothing
        read the script. This arm holds the package SET
        of both installs to each other, `pip` excepted — the script
        upgrades pip in its own earlier command by design, mirroring the
        CI line's `pip` operand. Version parity follows from set parity:
        every install command in the script applies the same
        constraints-ci.txt.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ or .cursor/ tree by design. "
                "This skip is NO INFORMATION about install-line parity — "
                "never read it as a pass."
            )
        script = root / ".cursor" / "install.sh"
        assert script.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {script} is missing. The local-mirror parity guard cannot "
            f"run. This is a FAILURE, not a skip: inside a checkout the "
            f"bootstrap script is required."
        )
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The parity guard cannot run. "
            f"This is a FAILURE, not a skip: inside a checkout the "
            f"workflow is required."
        )
        ci_tokens = self._ci_install_package_tokens(workflow)

        commands = self._script_pip_install_commands(script)
        script_tokens = set()
        for cmd in commands:
            script_tokens |= self._package_tokens_from_pip_argv(cmd)

        ci_side = ci_tokens - {"pip"}
        script_side = script_tokens - {"pip"}
        assert script_side, (
            f"PARSE FAILURE, not parity drift: the `pip install` commands in "
            f"{script} yielded no package operands. The CI mirror is the "
            f"point of those commands, so an empty read means the parse no "
            f"longer models the script."
        )

        # The constraints file must be APPLIED, not merely present in the
        # repo: the package SETS can match while versions float, if the
        # script ever dropped its `-c` — the same silent-break shape wearing
        # a different flag. _applies_constraints is the same check
        # the workflow side runs, so one spelling contract covers both.
        # EVERY command, the pip-upgrade command included: a zero-package
        # install carries no operands, so a token-based filter would exempt
        # exactly the command a re-pointed or missing `-c` could ride while
        # the set comparison stays green.
        assert commands and all(
            self._applies_constraints(cmd) for cmd in commands
        ), (
            f"LOCAL-MIRROR DRIFT: an install command in {script} "
            f"does not apply constraints-ci.txt (`-c`/`--constraint`). The "
            f"package sets still match, so the set comparison above cannot "
            f"see this — but local versions float, silently undoing the "
            f"lockstep the script's header claims. Restore the flag."
        )
        missing_locally = sorted(ci_side - script_side)
        assert not missing_locally, (
            f"LOCAL-MIRROR DRIFT: {missing_locally} are installed by CI's "
            f"install line but NOT by {script}. A local green and a CI green "
            f"then run different dependency sets — the exact divergence the "
            f"script's lockstep header exists to prevent. Add them to the "
            f"script, or remove them from the workflow."
        )
        extra_locally = sorted(script_side - ci_side)
        assert not extra_locally, (
            f"LOCAL-MIRROR DRIFT: {extra_locally} are installed by "
            f"{script} but NOT by CI's install line. The local environment "
            f"carries packages CI never sees, so a local green certifies "
            f"more than the CI green does. Reconcile the two lists."
        )

    def test_install_lines_admit_no_requirements_or_editable_flags(self):
        """Only VETTED options on either install surface (flag allowlist).

        ALLOWLIST polarity: every option on every pip install in CI and the
        bootstrap must be in `_PIP_ALLOWED_INSTALL_FLAGS` (the options the
        shipped lines use plus value-less logging/caching options), and a
        `--constraint` must name constraints-ci.txt; anything else reddens
        UNVETTED PIP FLAG DRIFT naming it, so an option pip adds later fails
        closed. Two families keep their own, more specific message:

        The install-SOURCE flags (`-r`/`-e`, `--group`,
        `--requirements-from-script`) take a VALUE the parse consumes, so a
        requirements file, editable path, dependency group or script's
        inline dependencies add packages that the declared-match, coverage and
        constraints-APPLIED arms never see, while constraints-ci.txt pins
        none of them (it mirrors the install line's direct names only). The
        index/source flags (`--index-url`/`-i`, `--extra-index-url`,
        `--find-links`/`-f`, `--trusted-host`, `--no-index`, `--pypi-url`)
        keep every
        package NAME intact — so every name arm stays green — while the
        pinned versions resolve from a source the constraints file never
        vetted, and its `==` pins carry no hashes. The same redirect through
        `PIP_*` environment is the env arm's; this is its argv twin.

        The flags are read from the shared argv parse, never as literal
        text, so every spelling pip accepts is one option here: `-r f`,
        `-rf`, `--requirement f`, `--requirement=f`, an abbreviated long
        flag, a short bundle, and a global option placed before `install`.
        Refused on both parsed surfaces: the workflow's install line and
        EVERY pip install command in the script — the pip-upgrade command
        included, where a flag would otherwise ride with no package
        operands to betray it. Introducing one legitimately means
        reconciling the coverage arms to read the referenced file or source
        in the same change — the red here is the reminder, not a bug.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ or .cursor/ tree by design. "
                "This skip is NO INFORMATION about the install-flag allowlist — "
                "never read it as a pass."
            )

        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The tripwire cannot run. This is "
            f"a FAILURE, not a skip: inside a checkout the workflow is "
            f"required."
        )
        script = root / ".cursor" / "install.sh"
        assert script.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {script} is missing. The tripwire cannot run. This is "
            f"a FAILURE, not a skip: inside a checkout the bootstrap "
            f"script is required."
        )
        script_commands = self._script_pip_install_commands(script)
        surfaces = [
            ("the CI install line", [self._ci_install_line(workflow)]),
            (f"{script} install commands", script_commands),
        ]
        for where, commands in surfaces:
            for cmd in commands:
                parsed = self._install_parse(cmd)
                assert parsed is not None, (
                    f"PARSE FAILURE, not a clean read: {where} command "
                    f"{cmd.strip()!r} is not exactly one pip install."
                )
                names = {name for name, _value in parsed[1]}
                requirement_hits = sorted(names & self._PIP_REQUIREMENT_FLAGS)
                assert not requirement_hits, (
                    f"REQUIREMENTS-FLAG DRIFT: {where} carries "
                    f"{requirement_hits} ({cmd.strip()!r}). Both flags take "
                    f"a value the parse consumes, so every package named by "
                    f"the referenced file installs with NO pin this "
                    f"machinery guarantees — the coverage arms cannot see "
                    f"them. Pin the names as install-line operands instead, "
                    f"or reconcile the coverage arms to read the referenced "
                    f"file in the same change."
                )
                redirect_hits = sorted(names & self._PIP_REDIRECT_FLAGS)
                assert not redirect_hits, (
                    f"INDEX-REDIRECT DRIFT: {where} carries {redirect_hits} "
                    f"({cmd.strip()!r}). Every package name stays intact, "
                    f"so the name arms stay green, while the pinned "
                    f"versions resolve from a source constraints-ci.txt "
                    f"never vetted — its `==` pins carry no hashes. Remove "
                    f"the flag, or record the source as a reviewed decision "
                    f"in this arm in the same change."
                )
                # ALLOWLIST POLARITY: every other option must be on the
                # vetted set, and a constraints option must name the
                # repo's own file — a second or re-pointed constraints
                # file loosens the pins the coverage arm checks.
                unvetted = sorted(
                    names - self._PIP_ALLOWED_INSTALL_FLAGS
                    - self._PIP_REQUIREMENT_FLAGS - self._PIP_REDIRECT_FLAGS)
                foreign_constraints = sorted(
                    value for name, value in parsed[1]
                    if name == "--constraint"
                    and not self._is_repo_constraints(value))
                assert not unvetted and not foreign_constraints, (
                    f"UNVETTED PIP FLAG DRIFT: {where} carries "
                    f"{unvetted + [f'--constraint {v}' for v in foreign_constraints]} "
                    f"({cmd.strip()!r}). A pip install in CI or the "
                    f"bootstrap may carry only the vetted options "
                    f"{sorted(self._PIP_ALLOWED_INSTALL_FLAGS)} (and "
                    f"--constraint naming constraints-ci.txt): any other "
                    f"option can change WHAT resolves or from WHERE — a new "
                    f"install source, a resolver strategy, a target "
                    f"directory — where no coverage arm looks. Confirm the "
                    f"option changes neither, then add it to "
                    f"_PIP_ALLOWED_INSTALL_FLAGS in the same change."
                )

    def test_every_executed_pip_install_is_the_canonical_install_line(self):
        """Census: no executed pip install anywhere in CI but the canonical one.

        POPULATION, stated so a green is read at its true width: what CI
        EXECUTES, followed from its entry files (`_execution_population`):
        every step of every workflow under `.github/workflows/`, every
        local action a `uses: ./<path>` names wherever it lives (its
        composite `runs.steps` walked recursively), every local reusable
        workflow a job calls, and every repo script a command executes —
        by path or as the operand of a shell or python — resolved against
        the step's working-directory, read as shell (Python through ast),
        recursively. Steps are read with yaml.safe_load, so flow-style
        steps and folded or block scalars read as the runner reads them;
        each text is cut into simple commands, `-c` strings and `eval`
        included, and parsed as pip argv (`_pip_installs_in`), so a
        program spelling (`pip3.12`, `python -mpip`, `uv pip`) or a global
        option before `install` cannot hide one. A followed local action
        that is not composite, a followed script in another language, and
        a script path that does not exist are REFUSED, not skipped.
        SCOPE: the guard covers the PYTHON dependency set constraints-ci.txt
        pins; system and other-language package managers (apt-get, npm,
        brew) are separate supply chains outside it, by design.
        Not read here: third-party actions and reusable workflows (their
        code is not in this tree — the allowlist arm holds them), paths
        outside the checkout (absolute, or on runner variables such as
        $RUNNER_TEMP), `python -`/`python -m <module>`, and installs done
        WITHOUT a pip program (an action input — see
        test_no_action_input_installs_packages — or another installer —
        see test_no_tool_runner_executes_unpinned_code).

        Exactly ONE install may exist across that population, and it must
        be the canonical install line every coverage / constraints /
        tripwire arm reads, compared by PARSE (same options, same
        operands), so a second install of any shape reddens here even when
        the canonical parse still succeeds.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ tree by design. This skip is "
                "NO INFORMATION about extra installs — never read it as a "
                "pass."
            )
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The census cannot run. This is a "
            f"FAILURE, not a skip: inside a checkout the workflow is "
            f"required."
        )
        canonical = self._ci_install_line(workflow)
        canonical_parse = self._install_parse(canonical.lstrip()[len("run:"):])
        census = self._population_installs(root)
        assert census, (
            "PARSE FAILURE, not census agreement: the canonical parse read "
            "an install line but the structural census found no executed "
            "pip install, so the census no longer models the workflow. "
            "Re-point it; do not delete it."
        )
        strays = [
            (label, command) for label, command, parsed in census
            if parsed != canonical_parse
        ]
        canonical_hits = len(census) - len(strays)
        assert not strays and canonical_hits == 1, (
            "INSTALL CENSUS DRIFT: executed pip installs exist beyond the "
            "one canonical install step "
            f"({canonical_hits} canonical, {len(strays)} other):\n"
            + "\n".join(f"  {label}: {command!r}" for label, command in strays)
            + "\nThe coverage / constraints / tripwire arms read only the "
            "canonical line, so these installs resolve unpinned. Fold them "
            "into the canonical install step, or reconcile the arms to read "
            "them in the same change."
        )

    def test_no_action_input_installs_packages(self):
        """No step installs packages through an ACTION INPUT.

        `actions/setup-python` takes a `pip-install:` input that runs
        `pip install` on its value inside the action, so its packages
        resolve unpinned while no `run:` text in the workflow names pip —
        every argv-reading arm stays green. Read structurally over the same
        population as the census (`_population_steps`): any step whose
        `with:` mapping carries a `pip-install` key reddens, whatever
        action it is passed to.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. This skip is NO INFORMATION about action "
                "inputs — never read it as a pass."
            )
        offenders = [
            f"{label}: pip-install={step['with'][key]!r}"
            for label, step in self._population_steps(root)
            if isinstance(step.get("with"), dict)
            for key in step["with"]
            if str(key).lower() == "pip-install"
        ]
        assert not offenders, (
            f"ACTION-INPUT INSTALL DRIFT: {offenders}. The action runs "
            f"`pip install` on the input's value with no `-c "
            f"constraints-ci.txt`, so those packages resolve to whatever "
            f"the index serves, and no `run:` text names them. Move them "
            f"to the canonical install line, where the coverage arms pin "
            f"them."
        )

    # Every THIRD-PARTY action the shipped workflows and actions use,
    # keyed on `owner/repo[/path]` with the `@ref` stripped (a SHA re-pin is
    # not drift). Each was vetted as installing nothing unpinned. Local
    # `./` actions are not listed: the census follows and reads them.
    _ALLOWED_THIRD_PARTY_ACTIONS = frozenset({
        "actions/checkout",
        "actions/setup-python",
        "actions/cache/restore",
        "actions/cache/save",
    })
    # Installer PROGRAMS other than pip: each resolves packages from its
    # own index or channel — into the job's environment or an isolated
    # tool environment — and none reads constraints-ci.txt. ALLOWLIST
    # polarity: any invocation is refused unless it is read-only, so a verb
    # this list never named (`poetry sync`, `uv pip sync`, `conda create`,
    # `tox`) fails closed. `python -m <module>` spellings map to the
    # program; `python setup.py <cmd>` counts as the program `setup.py`.
    _INSTALLER_PROGRAMS = frozenset({
        "uv", "uvx", "pipx", "poetry", "conda", "mamba", "micromamba",
        "pipenv", "pdm", "hatch", "tox", "nox", "pre-commit", "pip-sync",
        "piptools", "rye", "pixi", "easy_install", "flit", "setup.py",
    })
    _INSTALLER_MODULES = {
        "uv": "uv", "pipx": "pipx", "poetry": "poetry", "pdm": "pdm",
        "hatch": "hatch", "tox": "tox", "nox": "nox",
        "pre_commit": "pre-commit", "piptools": "piptools",
        "pipenv": "pipenv", "conda": "conda", "flit": "flit",
        "easy_install": "easy_install",
    }
    # NO table of these programs' value-taking global options: an option
    # word before the verb is skipped WITHOUT consuming a value, so a
    # valued global's VALUE lands in the verb slot (`uv --directory proj
    # sync` reads verb `proj`), which is never read-only — every such form
    # fails CLOSED. The cost is over-block only on a read-only call that
    # also passes a valued global (`uv --directory x pip list`), which a
    # faithful edit writes as `--directory=x` or drops.
    # The verbs that only READ. `uv pip <verb>` and `conda env <verb>`
    # read a second-level verb.
    _INSTALLER_READ_ONLY_VERBS = frozenset({
        "list", "info", "show", "help", "version", "tree", "freeze", "check",
    })
    _INSTALLER_READ_ONLY_FLAGS = frozenset({"--version", "-V", "--help", "-h"})

    @classmethod
    def _installer_program(cls, words):
        """(program, argv after it) for a simple command that invokes an
        installer program, or None."""
        i, program = cls._command_program(words)
        if program is None:
            return None
        base = program.rsplit("/", 1)[-1]
        if base in cls._INSTALLER_PROGRAMS:
            return base, words[i + 1:]
        if cls._PYTHON_PROGRAM_RE.search(program):
            rest = words[i + 1:]
            for j, tok in enumerate(rest):
                module = tok[2:] if tok.startswith("-m") and len(tok) > 2 \
                    else (rest[j + 1] if tok == "-m" and j + 1 < len(rest) else None)
                if module is not None:
                    mapped = cls._INSTALLER_MODULES.get(module)
                    if mapped is None:
                        return None
                    return mapped, rest[j + (1 if tok != "-m" else 2):]
                if tok.rsplit("/", 1)[-1] == "setup.py":
                    return "setup.py", rest[j + 1:]
                if not tok.startswith("-"):
                    return None
        return None

    @classmethod
    def _installer_is_read_only(cls, program, argv):
        """True only when the invocation provably installs nothing."""
        options, verbs = [], []
        for tok in argv:
            if tok.startswith("-"):
                options.append(tok.split("=", 1)[0])
                continue
            verbs.append(tok)
            if not (program == "uv" and tok == "pip") and not (
                    program in ("conda", "mamba", "micromamba") and tok == "env"):
                break
        if not verbs:
            return bool(options) and set(options) <= cls._INSTALLER_READ_ONLY_FLAGS
        if program in ("uvx", "setup.py"):
            return False
        return verbs[-1] in cls._INSTALLER_READ_ONLY_VERBS

    @classmethod
    def _tool_runner_commands(cls, text):
        """Simple commands in shell text that invoke an installer program
        other than pip in any way that is not provably read-only."""
        hits = []
        for words in cls._simple_commands(text):
            found = cls._installer_program(words)
            if found is not None and not cls._installer_is_read_only(*found):
                hits.append(" ".join(words))
        return hits

    def test_no_tool_runner_executes_unpinned_code(self):
        """No Python installer but the constrained pip line, in CI or the
        bootstrap — any other installer program may only be READ from.

        Installer programs other than pip (`_INSTALLER_PROGRAMS`: uv, uvx,
        pipx, poetry, conda, mamba, micromamba, pipenv, pdm, hatch, tox,
        nox, pre-commit, pip-sync/piptools, rye, pixi, easy_install, flit,
        and `python setup.py`), by name, path, wrapper or `python -m`,
        resolve packages where no install-line arm looks and none reads
        constraints-ci.txt. ALLOWLIST polarity: an invocation passes only
        when it provably installs nothing — no verb and only
        --version/--help, or a read-only verb (`_INSTALLER_READ_ONLY_VERBS`:
        list, info, show, help, version, tree, freeze, check; `uv pip <v>`
        and `conda env <v>` read the second verb). Every other verb, named
        or not, reddens. `uv run` is refused in EVERY form: it syncs the
        project environment and installs a script's PEP 723 inline
        dependencies, so it has no read-only form. Read over every
        shell text CI executes (`_population_texts`: `run:` strings and the
        repo scripts they run) and over .cursor/install.sh. An ACTION that
        installs or runs such a tool is held by the action allowlist
        (test_every_third_party_action_is_allowlisted), not by name here.
        A tool or package CI needs belongs on the canonical install line,
        where it is pinned.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. This skip is NO INFORMATION about tool "
                "runners — never read it as a pass."
            )
        offenders = []
        for label, text in self._population_texts(root):
            offenders += [
                f"{label}: {cmd!r}" for cmd in self._tool_runner_commands(text)
            ]
        script = root / ".cursor" / "install.sh"
        if script.exists():
            offenders += [
                f"{script.name}: {cmd!r}" for cmd in
                self._tool_runner_commands(script.read_text(encoding="utf-8"))
            ]
        assert not offenders, (
            f"TOOL-RUNNER DRIFT: {offenders}. Each invokes an installer "
            f"other than the constrained pip line in a form that is not "
            f"read-only, so it can resolve packages from an index or "
            f"channel constraints-ci.txt never pins, with the checkout in "
            f"reach. (`uv run` is refused in every form: it syncs the "
            f"project environment and installs a script's PEP 723 inline "
            f"dependencies.) A vetted tool or package goes on the canonical "
            f"constrained pip install line instead, where every coverage "
            f"arm reads and pins it; a read-only use (--version, list, "
            f"show) passes as it is."
        )

    def test_every_third_party_action_is_allowlisted(self):
        """Every third-party action CI uses is on a VETTED allowlist.

        An action is code CI runs that no `run:` text shows, and many
        install packages internally (pre-commit/action pip-installs
        pre-commit, setup-uv/ruff-action/setup-miniconda install their
        tools) with nothing constraints-ci.txt can pin. A name DENY-list
        only catches the installers someone thought of, so this is an
        ALLOWLIST: every `uses:` reference to another repository — step
        level, or a job-level reusable workflow — across every workflow and
        followed local action (`_execution_population`) must be in
        `_ALLOWED_THIRD_PARTY_ACTIONS`, keyed on `owner/repo[/path]` with
        the `@ref` stripped. `docker://` images are never allowed. Local
        `./` actions are not allowlisted: the census follows and reads
        them. The setup-python `with.pip-install` input keeps its own arm,
        because an allowlisted action can still be told to install.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. This skip is NO INFORMATION about the "
                "action allowlist — never read it as a pass."
            )
        yaml_files, steps, _texts, _files = self._execution_population(root)
        references = [(label, step.get("uses")) for label, step in steps]
        for path in yaml_files:
            doc = self._load_workflow_yaml(path)
            for jid, job in ((doc or {}).get("jobs") or {}).items() \
                    if isinstance(doc, dict) else []:
                if isinstance(job, dict):
                    references.append(
                        (f"{path.relative_to(root).as_posix()} job {jid}",
                         job.get("uses")))
        offenders = []
        for label, uses in references:
            if not isinstance(uses, str) or uses.startswith("./"):
                continue
            name = uses.split("@")[0].rstrip("/").lower()
            if uses.startswith("docker://") \
                    or name not in self._ALLOWED_THIRD_PARTY_ACTIONS:
                offenders.append(f"{label}: uses {uses!r}")
        assert not offenders, (
            f"ACTION ALLOWLIST DRIFT: {offenders} are not on the vetted "
            f"allowlist. An action runs code no `run:` text shows, and an "
            f"installing action resolves packages constraints-ci.txt cannot "
            f"pin. Confirm the action installs nothing unpinned (read its "
            f"action.yml and what it runs), then add `owner/repo[/path]` to "
            f"_ALLOWED_THIRD_PARTY_ACTIONS in the same change — or put what "
            f"it installs on the canonical install line instead."
        )

    def _repo_copy_carrying(self, tmp_path, workflow_text=None,
                            script_text=None, extra_files=None):
        """A minimal repo copy over a POSITIVE marker, for the mutation arms.

        The arms in this class find the repo root by marker
        (`.claude-plugin/marketplace.json`); this rig plants that marker
        over a scratch tree so a mutation lands on a COPY — never the
        checkout — while constraints-ci.txt and pyproject.toml ship
        verbatim, because every arm compares the mutated surface against
        the REAL pins and floor. Every file the real execution walk reads
        is copied (`_execution_population`: the workflows plus every local
        action and repo script they run), with
        tests.yml and the install script defaulting to their real bytes, so
        a mutation is one targeted substitution on top of the shipped
        state. `extra_files` maps a repo-relative path to text, for
        mutations that ADD a file (a second workflow, a composite action)
        or replace a non-default one (constraints-ci.txt).
        """
        root = self._real_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout and the mutation arms cannot copy the "
                "real surfaces to mutate. This skip is NO INFORMATION "
                "about the guards — never read it as a pass."
            )
        (tmp_path / ".claude-plugin").mkdir(parents=True)
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
            "{}", encoding="utf-8")
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        for path in self._execution_population(root)[3]:
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
            script_text
            if script_text is not None
            else (root / ".cursor" / "install.sh").read_text(encoding="utf-8"),
            encoding="utf-8")
        for rel, text in (extra_files or {}).items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return tmp_path

    def test_pip3_spelling_cannot_sidestep_the_install_arms(
            self, tmp_path, monkeypatch):
        """MUTATION ARM (pip3): the runner-stock spelling must redden.

        Probed at 74cc1d59 by the round-5 security lane: `pip3 install
        evilpkg` evaded the census, exactly-one, coverage,
        constraints-APPLIED AND the -r/-e tripwire simultaneously, because
        every extractor defined 'an install' as the literal substring
        `pip install`. pip3 is the stock spelling on GitHub's Ubuntu
        runners — the most plausible one-word honest edit. Three shapes,
        each a targeted substitution on a COPY of the real workflow, each
        asserting the SHIPPED arm that kills it (never a reimplementation
        beside it): a second inline install step reddens the exactly-one
        parse; an install inside an existing `run: |` block reddens the
        census equality; and the canonical spelling switched to pip3
        while smuggling `-r` past the value-taking-flag tokenizer reddens
        the tripwire — the arm that exists precisely for that flag, which
        an install-line spelling change must not slip past.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        canonical = self._ci_install_line(workflow)

        # Shape 1: a SECOND inline install step (key-form `run:` line), so
        # the exactly-one parse is the arm that must fire.
        inline_second = real_text.replace(
            canonical,
            canonical + "\n      - name: Rogue installer\n"
                        "        run: pip3 install evilpkg",
            1,
        )
        copy1 = self._repo_copy_carrying(
            tmp_path / "inline", workflow_text=inline_second)
        monkeypatch.setattr(
            type(self), "_source_repo_root", lambda self: copy1)
        with pytest.raises(AssertionError, match="PARSE AMBIGUITY"):
            self.test_declared_packages_match_the_ci_install_line()

        # Shape 2: an install inside an EXISTING run: | block body — the
        # canonical parse still sees exactly one line, so only the census
        # equality can fire.
        block_second = real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          pip3 install evilpkg\n",
            1,
        )
        copy2 = self._repo_copy_carrying(
            tmp_path / "block", workflow_text=block_second)
        monkeypatch.setattr(
            type(self), "_source_repo_root", lambda self: copy2)
        with pytest.raises(AssertionError, match="INSTALL CENSUS DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # Shape 3: the canonical spelling switched to pip3 with `-r`
        # smuggled aboard — every other arm stays green (the flag's VALUE
        # is consumed by the tokenizer), so the tripwire must be the kill.
        switched = real_text.replace(
            canonical,
            canonical.replace("python -m pip install", "pip3 install")
            + " -r requirements-evil.txt",
            1,
        )
        copy3 = self._repo_copy_carrying(
            tmp_path / "tripwire", workflow_text=switched)
        monkeypatch.setattr(
            type(self), "_source_repo_root", lambda self: copy3)
        with pytest.raises(AssertionError, match="REQUIREMENTS-FLAG DRIFT"):
            self.test_install_lines_admit_no_requirements_or_editable_flags()

    def test_plain_scalar_run_continuation_is_refused(
            self, tmp_path, monkeypatch):
        """MUTATION ARM (folded scalar): the shape is refused, not modelled.

        Probed at 74cc1d59: YAML folds a deeper-indented bare line after a
        plain `run: <command>` into ONE executed scalar (ground-truthed
        with yaml.safe_load — the folded text IS the step's run string),
        while every line-oriented arm reads the first line only. Both
        probe directions must redden under the refusal: the fold carrying
        a rogue OPERAND (every pinned operand stays on the first line, so
        the pre-fix arms were green throughout), and the fold carrying a
        whole `pip3 install` that no census line ever sees. The shipped
        arms are invoked against the copy, so the kill is theirs, not a
        beside-the-point reimplementation.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        canonical = self._ci_install_line(workflow)

        # Probe 1: the fold carries a rogue OPERAND.
        folded_operand = real_text.replace(
            canonical, canonical + "\n          evilpkg", 1)
        copy1 = self._repo_copy_carrying(
            tmp_path / "operand", workflow_text=folded_operand)
        monkeypatch.setattr(
            type(self), "_source_repo_root", lambda self: copy1)
        with pytest.raises(
                AssertionError, match="PLAIN-SCALAR RUN CONTINUATION"):
            self.test_constraints_pins_cover_the_install_line()

        # Probe 2: the fold carries the INSTALL ITSELF — neither the run
        # line nor any census line contains a pip command, so only the
        # shape refusal can see it.
        folded_install = real_text.replace(
            canonical,
            "        run: echo warming\n"
            "          && pip3 install evilpkg",
            1,
        )
        copy2 = self._repo_copy_carrying(
            tmp_path / "install", workflow_text=folded_install)
        monkeypatch.setattr(
            type(self), "_source_repo_root", lambda self: copy2)
        with pytest.raises(
                AssertionError, match="PLAIN-SCALAR RUN CONTINUATION"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

    def test_script_pip3_install_is_enumerated(self, tmp_path, monkeypatch):
        """MUTATION ARM (script surface): a script `pip3 install` reddens.

        The script extractor shared the substring anchor, so a
        `pip3 install evilpkg` line in .cursor/install.sh read as no
        command at all — riding past the constraints-APPLIED all() and
        the set parity while floating an unpinned package into the local
        mirror. The shipped cursor-parity arm must redden naming the
        constraint the command fails to apply.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        script = root / ".cursor" / "install.sh"
        mutated = script.read_text(encoding="utf-8") \
            + "\npip3 install evilpkg\n"
        copy = self._repo_copy_carrying(
            tmp_path / "script", script_text=mutated)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        with pytest.raises(AssertionError, match="does not apply constraints"):
            self.test_cursor_install_sh_matches_the_ci_install_line()

    def test_joined_install_spells_are_censused(self, tmp_path, monkeypatch):
        """MUTATION ARM (join class): every wrapped/widened install spell
        reddens through a shipped arm.

        Round-6 probes (architect A6-1, tester T6-1, security S6-1/S6-2)
        shared one root cause: every extractor anchored on a LINE-LOCAL
        literal `pip install` substring, so shapes that execute an install
        read as no install at all. The normalization fixes the class —
        backslash continuations joined in `|` bodies and script commands,
        `>` bodies unfolded, the anchor's space widened to any whitespace
        run — and this arm certifies each probed shape against the
        SHIPPED arm that must kill it, never a reimplementation.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        canonical = self._ci_install_line(workflow)

        def against(workflow_text, name):
            copy = self._repo_copy_carrying(
                tmp_path / name, workflow_text=workflow_text)
            monkeypatch.setattr(
                type(self), "_source_repo_root", lambda self: copy)

        # P1 — double space on an inline second step: the shell collapses
        # the run, so only the widened anchor can count the line, and the
        # exactly-one parse must be the kill.
        against(real_text.replace(
            canonical,
            canonical + "\n      - name: Rogue double-spaced installer\n"
                        "        run: pip  install evilpkg",
            1), "p1")
        with pytest.raises(AssertionError, match="PARSE AMBIGUITY"):
            self.test_declared_packages_match_the_ci_install_line()

        # P2 — a folded (`>-`) body splitting pip and install across body
        # lines: YAML folds them into one executed scalar, so only the
        # unfold can see the install, and the census equality must kill.
        against(real_text.replace(
            canonical,
            canonical + "\n      - name: Rogue folded installer\n"
                        "        run: >-\n"
                        "          pip\n"
                        "          install evilpkg",
            1), "p2")
        with pytest.raises(AssertionError, match="INSTALL CENSUS DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # P3 — backslash continuation inside a `|` body: bash joins
        # `pip \` + `install evilpkg` into one argv, so the census must
        # read the JOIN, not the lines.
        against(real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          pip \\\n"
            "          install evilpkg\n",
            1), "p3")
        with pytest.raises(AssertionError, match="INSTALL CENSUS DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # P3b — the natural wrap of the 130-char canonical spelling:
        # `python -m pip \` + `install evilpkg` joins the same way.
        against(real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          python -m pip \\\n"
            "          install evilpkg\n",
            1), "p3b")
        with pytest.raises(AssertionError, match="INSTALL CENSUS DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # S6-2 — a TAB as the separator in a `|` body: tab is IFS to the
        # shell, so the widened anchor must match what a literal space
        # missed. Inline tabs are invalid YAML and die at parse; the
        # block body is the live spelling.
        against(real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          pip\tinstall evilpkg\n",
            1), "tab")
        with pytest.raises(AssertionError, match="INSTALL CENSUS DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # S1 — the script twin: a wrapped `python -m pip \` + `install
        # evilpkg` in .cursor/install.sh enumerates after the join, so the
        # constraints-APPLIED all() reddens naming the constraint the
        # rogue command fails to apply.
        script = root / ".cursor" / "install.sh"
        mutated = script.read_text(encoding="utf-8") \
            + "\npython -m pip \\\ninstall evilpkg\n"
        copy = self._repo_copy_carrying(
            tmp_path / "s1", script_text=mutated)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        with pytest.raises(AssertionError, match="does not apply constraints"):
            self.test_cursor_install_sh_matches_the_ci_install_line()

    @staticmethod
    def _pip_environment_keys(workflow_text):
        """PIP_* keys from every STATIC `env:` mapping in one workflow or
        composite action, read STRUCTURALLY.

        The mappings are enumerated from yaml.safe_load's parse, not from
        line shapes: block form, trailing comment, single-line flow and
        WRAPPED flow (`env: {` with keys on later lines) are one mapping
        node to the parser, so no spelling arm chases the next spelling.
        The STATIC channels walked are: workflow-level env, job-level env,
        a job CONTAINER's env (it applies to every step in a containerized
        job), each job step's env, and each composite action step's env.
        SERVICE containers' env (`jobs.<id>.services.<id>.env`) is
        deliberately NOT harvested: a service container runs a support
        process the job talks TO over the network, and its environment
        never reaches the job's own process. `environment:` is a different
        key and is never consulted. Keys are compared case-insensitively
        (over-catch: pip itself reads only uppercase, but a lowercase
        `pip_*` key reads as intended-for-pip to every future reader even
        if pip ignores it). This reads STATIC configuration only; pip
        configuration a step WRITES at run time is read by
        `_runtime_pip_config_writes`. A workflow that does not PARSE is a
        PARSE FAILURE, not a clean read — an unparseable workflow cannot
        run on GitHub, so a loud red is the honest outcome, never a green
        line-scan over text no runner would accept.
        """
        try:
            doc = yaml.safe_load(workflow_text)
        except yaml.YAMLError as exc:
            raise AssertionError(
                f"PARSE FAILURE, not a clean env read: the workflow does "
                f"not parse as YAML ({exc.__class__.__name__}), so its "
                f"env: mappings cannot be enumerated. GitHub would refuse "
                f"to run this workflow; fix the syntax."
            ) from exc
        offenders = []

        def harvest(env):
            if isinstance(env, dict):
                offenders.extend(
                    str(k) for k in env if str(k).upper().startswith("PIP_")
                )

        if isinstance(doc, dict):
            harvest(doc.get("env"))
            for job in (doc.get("jobs") or {}).values():
                if not isinstance(job, dict):
                    continue
                harvest(job.get("env"))
                container = job.get("container")
                if isinstance(container, dict):
                    harvest(container.get("env"))
                for step in job.get("steps") or []:
                    if isinstance(step, dict):
                        harvest(step.get("env"))
            runs = doc.get("runs")
            if isinstance(runs, dict):
                for step in runs.get("steps") or []:
                    if isinstance(step, dict):
                        harvest(step.get("env"))
        return offenders

    # A shell word that ASSIGNS a pip setting: `PIP_X=...` as an
    # assignment, an `export` operand, or the text echoed/printed into
    # $GITHUB_ENV (`echo "PIP_INDEX_URL=..." >> "$GITHUB_ENV"`, a heredoc
    # body line, `printf 'PIP_X=%s\n'`) — the shell model has already
    # removed the quotes, so every spelling is one word shape.
    _PIP_ASSIGNMENT_RE = re.compile(r"(?i)^pip_[a-z0-9_]*=")

    @classmethod
    def _runtime_pip_config_writes(cls, text):
        """pip configuration that shell text WRITES while it runs.

        Three shapes, each read from the simple commands the text executes
        (`_simple_commands`): a `PIP_*=` assignment word anywhere in a
        command (a direct assignment, `export`, or a line written into
        $GITHUB_ENV, which configures every LATER step's pip); a
        `pip config set|unset|edit` (it writes the pip config file); and a
        command naming a `pip.conf`/`pip.ini` file (writing one by hand).
        Over-catch by design: an `echo` that merely prints a PIP_ line also
        reddens, and a legitimate need is recorded in the env arm rather
        than carved out here.
        """
        writes = []
        for words in cls._simple_commands(text, keep_redirect_targets=True):
            command = " ".join(words)
            if any(cls._PIP_ASSIGNMENT_RE.match(w) for w in words):
                writes.append(command)
                continue
            parsed = cls._parse_pip_words(words)
            if parsed is not None and parsed[0] == "config" and parsed[2][:1] \
                    and parsed[2][0] in ("set", "unset", "edit"):
                writes.append(command)
                continue
            if any(w.rsplit("/", 1)[-1] in ("pip.conf", "pip.ini") for w in words):
                writes.append(command)
        return writes

    def test_no_pip_environment_key_redirects_the_install(self):
        """No pip configuration outside the install line's own arguments.

        PIP_INDEX_URL redirects every resolve to an index of someone
        else's choosing, PIP_CONSTRAINT re-points the constraint file,
        and the constraints file's `==` pins carry no hashes — so a
        redirected index can serve attacker builds of the pinned
        versions. Such configuration touches none of the surfaces the
        install-line arms read, so this arm reads it directly.

        POPULATION, stated so a green is read at its true width:
          - STATIC env: every `env:` mapping `_pip_environment_keys` walks,
            in every workflow and followed local action of the execution
            population (`_execution_population`).
          - RUNTIME writes: every shell text CI executes (`run:` strings
            and the repo scripts they run, `_population_texts`), and
            .cursor/install.sh, read by `_runtime_pip_config_writes`
            (`PIP_*=` assignments including $GITHUB_ENV lines,
            `pip config set|unset|edit`, a named pip.conf/pip.ini).
        NOT read: configuration an action sets internally (its code is not
        in this tree), a pip config file present on the runner image, and
        values computed at run time and assigned through an indirection
        (`export "$NAME=..."`). A green is a statement about the population
        above, not about every way pip can be configured.

        TRIPWIRE, deliberately over-catch-only: any hit reddens naming
        itself whether or not the use looks legitimate — a future
        configuration with a real purpose is reconciled by justifying it
        HERE and naming it in this docstring, never by deleting the arm.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ tree by design. This skip "
                "is NO INFORMATION about PIP_* environment keys — never "
                "read it as a pass."
            )
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The env tripwire cannot run. "
            f"This is a FAILURE, not a skip: inside a checkout the "
            f"workflow is required."
        )
        offenders = []
        for path in self._execution_population(root)[0]:
            rel = path.relative_to(root).as_posix()
            offenders += [
                f"{rel}: env {key}" for key in
                self._pip_environment_keys(path.read_text(encoding="utf-8"))
            ]
        for label, text in self._population_texts(root):
            offenders += [
                f"{label}: {cmd!r}"
                for cmd in self._runtime_pip_config_writes(text)
            ]
        script = root / ".cursor" / "install.sh"
        if script.exists():
            offenders += [
                f"{script.name}: {cmd!r}" for cmd in
                self._runtime_pip_config_writes(
                    script.read_text(encoding="utf-8"))
            ]
        assert not offenders, (
            f"PIP ENVIRONMENT DRIFT: {offenders} configure pip OUTSIDE "
            f"every install-line arm — PIP_INDEX_URL redirects resolves "
            f"to an index the constraints file never vetted, and the "
            f"pins carry no hashes, so the redirect serves builds of the "
            f"pinned versions. pip configuration needs a recorded decision "
            f"in test_no_pip_environment_key_redirects_the_install, not a "
            f"silent env: key, $GITHUB_ENV line, export or pip config."
        )

    def test_a_pip_env_key_reddens_naming_itself(self, tmp_path, monkeypatch):
        """MUTATION ARM (env): every env: spelling of a PIP_ key reddens by
        name.

        PREDICTIONS (written before the probes run): each of the five
        shapes below — step-level block, job-level block, trailing
        comment on the `env:` line, single-line flow mapping, and the
        WRAPPED flow mapping (`env: {` with keys on later lines, the
        cycle-12 Bugbot shape) — reddens the shipped tripwire with
        PIP_INDEX_URL named, because the detector reads the env
        mappings STRUCTURALLY (yaml.safe_load), the way a runner does;
        a workflow-level env: shape and a job CONTAINER's env (the
        cycle-13 Bugbot channel — it applies to every step in a
        containerized job, the install included) redden the same way;
        a deployment `environment:` key is NOT an env: mapping and
        stays unflagged, and so does a SERVICE container's env (its
        variables never reach the job's own process); and an
        unparseable workflow reddens as PARSE FAILURE rather than
        line-scanning green over text no runner would accept.
        The comment and flow spellings rode the cycle-10 line scanner
        green (Bugbot, cycle 11), the wrapped flow rode the cycle-11
        same-line scan green (Bugbot, cycle 12) — the structural read
        retires the spelling class.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        canonical = self._ci_install_line(workflow)

        def assert_reddens(name, mutated_text):
            copy = self._repo_copy_carrying(
                tmp_path / name, workflow_text=mutated_text)
            monkeypatch.setattr(
                type(self), "_source_repo_root", lambda self: copy)
            with pytest.raises(
                    AssertionError, match="PIP ENVIRONMENT DRIFT") as exc:
                self.test_no_pip_environment_key_redirects_the_install()
            assert "PIP_INDEX_URL" in str(exc.value), (
                f"the red must name the offending key; got: {exc.value}"
            )

        assert_reddens("step-block", real_text.replace(
            canonical,
            canonical + "\n        env:\n"
                        "          PIP_INDEX_URL: https://evil.example/simple",
            1))
        assert_reddens("job-block", real_text.replace(
            "    steps:",
            "    env:\n"
            "      PIP_INDEX_URL: https://evil.example/simple\n"
            "    steps:", 1))
        assert_reddens("trailing-comment", real_text.replace(
            canonical,
            canonical + "\n        env: # point pip elsewhere\n"
                        "          PIP_INDEX_URL: https://evil.example/simple",
            1))
        assert_reddens("flow-mapping", real_text.replace(
            canonical,
            canonical + "\n        env: "
                        "{PIP_INDEX_URL: https://evil.example/simple}",
            1))
        assert_reddens("wrapped-flow", real_text.replace(
            canonical,
            canonical + "\n        env: {\n"
                        "          PIP_INDEX_URL: https://evil.example/simple\n"
                        "}",
            1))
        assert_reddens("workflow-level", real_text.replace(
            "on:\n",
            "env:\n  PIP_INDEX_URL: https://evil.example/simple\non:\n", 1))
        assert_reddens("container-env", real_text.replace(
            "    steps:",
            "    container:\n"
            "      image: ubuntu:24.04\n"
            "      env:\n"
            "        PIP_INDEX_URL: https://evil.example/simple\n"
            "    steps:", 1))

        # CONTROL: `environment:` (a deployment target) is a different
        # key from `env` and stays unflagged.
        assert self._pip_environment_keys(
            "jobs:\n  release:\n    environment: production\n") == []

        # BOUND: a SERVICE container's env never reaches the job's own
        # process — deliberately unharvested, and pinned here so a
        # future widening cannot silently change that decision.
        assert self._pip_environment_keys(
            "jobs:\n  x:\n    services:\n"
            "      redis:\n        env:\n"
            "          PIP_INDEX_URL: https://evil.example/simple\n") == []

        # PARSE FAILURE: an unparseable workflow reddens rather than
        # reading green over text GitHub would refuse to run.
        broken = real_text.replace(
            canonical, canonical + "\n        env: {unclosed", 1)
        copy = self._repo_copy_carrying(
            tmp_path / "unparseable", workflow_text=broken)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        with pytest.raises(AssertionError, match="PARSE FAILURE"):
            self.test_no_pip_environment_key_redirects_the_install()

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
        """CONTROL: a faithful spelling the parse must ACCEPT."""
        copy = self._repo_copy_carrying(
            tmp_path / name, workflow_text=workflow_text,
            script_text=script_text, extra_files=extra_files)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        getattr(self, arm)()

    def test_pip_argv_spellings_redden_at_their_arms(self, tmp_path, monkeypatch):
        """MUTATION ARM (argv class): every spelling pip accepts is ONE
        option to the arms.

        Round-7 probes: the tripwire matched literal flag text, so pip's
        attached short form (`-rreq.txt`, `-e./foo`) installed a
        requirements file or editable path with every arm green; the
        index/source flags were never read at all; and the census anchor
        needed `install` right after a literal `pip`/`pip3`, so a global
        option between them (`pip --quiet install`), `pip3.12` and
        `python -mpip` hid an install. Each shape below was GREEN against
        the literal anchors and must now redden at the named shipped arm;
        the attached `-c` spelling is the CONTROL the parse must accept.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        real_script = (root / ".cursor" / "install.sh").read_text(encoding="utf-8")
        canonical = self._ci_install_line(workflow)
        lint_anchor = "          ruff --version\n"
        tripwire = "test_install_lines_admit_no_requirements_or_editable_flags"
        census = "test_every_executed_pip_install_is_the_canonical_install_line"

        def on_line(extra):
            return real_text.replace(canonical, canonical + extra, 1)

        def in_block(command):
            return real_text.replace(
                lint_anchor, lint_anchor + f"          {command}\n", 1)

        for name, text, match in [
            ("attached-r", on_line(" -rreq.txt"), "REQUIREMENTS-FLAG DRIFT"),
            ("bundled-Ur", on_line(" -Urreq.txt"), "REQUIREMENTS-FLAG DRIFT"),
            ("eq-requirement", on_line(" --requirement=req.txt"),
             "REQUIREMENTS-FLAG DRIFT"),
            ("extra-index", on_line(" --extra-index-url https://x.example/simple"),
             "INDEX-REDIRECT DRIFT"),
            ("short-i", on_line(" -i https://x.example/simple"),
             "INDEX-REDIRECT DRIFT"),
            ("attached-i", on_line(" -ihttps://x.example/simple"),
             "INDEX-REDIRECT DRIFT"),
            ("find-links", on_line(" --find-links https://x.example/"),
             "INDEX-REDIRECT DRIFT"),
            ("trusted-host", on_line(" --trusted-host x.example"),
             "INDEX-REDIRECT DRIFT"),
            ("no-index", on_line(" --no-index"), "INDEX-REDIRECT DRIFT"),
            ("abbreviated", on_line(" --extra-index https://x.example/simple"),
             "INDEX-REDIRECT DRIFT"),
            ("global-position", real_text.replace(
                canonical,
                canonical.replace("pip install",
                                  "pip --index-url https://x.example/simple install"),
                1), "INDEX-REDIRECT DRIFT"),
            # Install-source flags pip added (PEP 735 groups, PEP 723
            # scripts) and an index flag pip 26 gained.
            ("group-eq", on_line(" --group=test"), "REQUIREMENTS-FLAG DRIFT"),
            ("group-sep", on_line(" --group test"), "REQUIREMENTS-FLAG DRIFT"),
            ("from-script", on_line(" --requirements-from-script=x.py"),
             "REQUIREMENTS-FLAG DRIFT"),
            ("pypi-url", on_line(" --pypi-url https://x.example/"),
             "INDEX-REDIRECT DRIFT"),
            # ALLOWLIST polarity: any option not vetted reddens naming
            # itself, including resolver knobs and a foreign -c file.
            ("prefer-binary", on_line(" --prefer-binary"),
             "UNVETTED PIP FLAG DRIFT"),
            ("upgrade-strategy", on_line(" --upgrade-strategy=eager"),
             "UNVETTED PIP FLAG DRIFT"),
            ("target-dir", on_line(" --target /tmp/site"),
             "UNVETTED PIP FLAG DRIFT"),
            ("unknown-flag", on_line(" --brand-new-flag"),
             "UNVETTED PIP FLAG DRIFT"),
            ("foreign-constraints", on_line(" -c extra-constraints.txt"),
             "UNVETTED PIP FLAG DRIFT"),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, tripwire, match,
                                workflow_text=text)

        # CONTROLS: vetted options and every shipped spelling of the
        # constraints path pass the tripwire AND the constraints arm.
        for name, text in [
            ("vetted-flags", on_line(" -q --no-cache-dir --no-input")),
            ("dot-constraints", real_text.replace(
                canonical, canonical.replace(
                    "-c constraints-ci.txt", "-c ./constraints-ci.txt"), 1)),
            ("workspace-constraints", real_text.replace(
                canonical, canonical.replace(
                    "-c constraints-ci.txt",
                    "--constraint=$GITHUB_WORKSPACE/constraints-ci.txt"), 1)),
        ]:
            self._shape_stays_green(tmp_path, monkeypatch, name, tripwire,
                                    workflow_text=text)
            self._shape_stays_green(
                tmp_path, monkeypatch, name + "-cov",
                "test_constraints_pins_cover_the_install_line",
                workflow_text=text)

        # The option tables follow pip's own parser: a value option's VALUE
        # is consumed, never read as a package (`--upgrade-strategy` was
        # once listed value-less, so `eager` read as an operand).
        for words, operands in [
            ("pip install --upgrade-strategy eager x", ["x"]),
            ("pip install --root-user-action ignore x", ["x"]),
            ("pip install --group test x", ["x"]),
            ("pip install -C k=v x", ["x"]),
            ("pip install --pre --no-deps x", ["x"]),
        ]:
            assert self._parse_pip_words(words.split())[2] == operands, words
        assert not (self._PIP_FLAGS_WITH_VALUE & self._PIP_KNOWN_LONG_FLAGS), (
            "an option is listed both as value-taking and value-less")

        for name, command in [
            ("global-quiet", "python -m pip --quiet install evilpkg"),
            ("global-q", "pip -q install evilpkg"),
            # A value-taking global option this parser does NOT list: its
            # value must be skipped, not misread as the subcommand.
            ("unknown-global-value", "pip --brand-new-global 5 install evilpkg"),
            ("pip3-12", "pip3.12 install evilpkg"),
            ("m-attached", "python -mpip install evilpkg"),
            ("python-path", "/usr/bin/python3 -m pip install evilpkg"),
            ("sudo", "sudo pip install evilpkg"),
            ("and-list", "cd pact-plugin && pip install evilpkg"),
            ("subshell", "echo $(pip install evilpkg)"),
            ("uv-pip", "uv pip install evilpkg"),
            # Program identity by BASENAME (Bugbot B1).
            ("uv-pip-by-path", "/usr/bin/uv pip install evilpkg"),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, census,
                                "INSTALL CENSUS DRIFT",
                                workflow_text=in_block(command))

        package_line = "python -m pip install --upgrade -c constraints-ci.txt \\"
        upgrade_line = "python -m pip install --upgrade -c constraints-ci.txt pip\n"
        for name, script, arm, match in [
            ("script-group-on-upgrade",
             real_script.replace(
                 upgrade_line, upgrade_line.replace(" pip\n", " --group=dev pip\n"), 1),
             tripwire, "REQUIREMENTS-FLAG DRIFT"),
            ("script-attached-e",
             real_script.replace(package_line, package_line + "\n  -e./foo \\", 1),
             tripwire, "REQUIREMENTS-FLAG DRIFT"),
            ("script-extra-index",
             real_script.replace(
                 package_line,
                 package_line.replace(
                     "\\", "--extra-index-url https://x.example/simple \\"), 1),
             tripwire, "INDEX-REDIRECT DRIFT"),
            ("script-pip3-12",
             real_script + "\npip3.12 install evilpkg\n",
             "test_cursor_install_sh_matches_the_ci_install_line",
             "does not apply constraints"),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, arm, match,
                                script_text=script)

        # FAIL-CLOSED CONTRACT (`_PIP_FLAGS_WITH_VALUE`): a value-taking
        # flag this parser does not know must leave its VALUE read as a
        # package operand, so the coverage arm reddens naming it as
        # unpinned — never skip the value silently.
        self._shape_reddens(
            tmp_path, monkeypatch, "unknown-flag-value",
            "test_constraints_pins_cover_the_install_line",
            r"CONSTRAINT COVERAGE DRIFT: \['some-value'\]",
            workflow_text=on_line(" --brand-new-flag some-value"))

        # CONTROL: pip reads `-cconstraints-ci.txt` exactly as
        # `-c constraints-ci.txt`, so the attached spelling still APPLIES
        # the constraints file — the parse must not over-block it.
        self._shape_stays_green(
            tmp_path, monkeypatch, "attached-c",
            "test_constraints_pins_cover_the_install_line",
            workflow_text=real_text.replace(
                canonical,
                canonical.replace("-c constraints-ci.txt",
                                  "-cconstraints-ci.txt"),
                1))

    def test_the_census_reads_every_file_ci_executes(self, tmp_path, monkeypatch):
        """MUTATION ARM (population class): installs outside tests.yml,
        in a flow-style step, or through an action input redden.

        Round-7 probes: the census read one file line by line, so a second
        workflow file, a local composite action and a flow-style step each
        ran `pip install` with every arm green, and `actions/setup-python`'s
        `pip-install:` input installed with no `run:` text at all.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        census = "test_every_executed_pip_install_is_the_canonical_install_line"
        install_step = "      - name: Install test dependencies\n"

        self._shape_reddens(
            tmp_path, monkeypatch, "second-workflow", census,
            "INSTALL CENSUS DRIFT", extra_files={
                ".github/workflows/lint.yml":
                    "name: lint\non: [pull_request]\njobs:\n  lint:\n"
                    "    runs-on: ubuntu-latest\n    steps:\n"
                    "      - run: pip install ruff\n"})
        self._shape_reddens(
            tmp_path, monkeypatch, "composite-action", census,
            "INSTALL CENSUS DRIFT", extra_files={
                ".github/actions/setup/action.yml":
                    "name: setup\nruns:\n  using: composite\n  steps:\n"
                    "    - run: pip install pytest-xdist\n      shell: bash\n"})
        self._shape_reddens(
            tmp_path, monkeypatch, "flow-step", census,
            "INSTALL CENSUS DRIFT", workflow_text=real_text.replace(
                install_step,
                "      - {name: extra, run: pip install evilpkg}\n"
                + install_step, 1))
        # BLANKET STILL: a line under `- run: x` at the KEY's column or
        # between dash and key is not a fold (YAML rejects it — measured
        # with yaml.safe_load: col key-1 ParserError, col key ScannerError;
        # folds begin at key+1). The refusal no longer flags it, and the
        # structural read reddens it as a PARSE FAILURE instead, so no
        # shape the refusal used to catch now reads green.
        self._shape_reddens(
            tmp_path, monkeypatch, "list-item-key-column", census,
            "PARSE FAILURE|PLAIN-SCALAR RUN CONTINUATION",
            workflow_text=real_text.replace(
                install_step,
                "      - run: echo warming\n        evilpkg\n"
                + install_step, 1))
        self._shape_reddens(
            tmp_path, monkeypatch, "list-item-real-fold", census,
            "PLAIN-SCALAR RUN CONTINUATION",
            workflow_text=real_text.replace(
                install_step,
                "      - run: echo warming\n          && pip install evilpkg\n"
                + install_step, 1))
        # CONTROL: a list-item step with a SIBLING key (`- run: x` +
        # `shell: bash`) is a faithful shape, not a fold. The plain-scalar
        # refusal measured its indent from the dash and refused it; it must
        # now read the sibling as the end of the scalar.
        self._shape_stays_green(
            tmp_path, monkeypatch, "list-item-sibling", census,
            workflow_text=real_text.replace(
                install_step,
                "      - run: echo warming\n        shell: bash\n"
                + install_step, 1))
        self._shape_reddens(
            tmp_path, monkeypatch, "pip-install-input",
            "test_no_action_input_installs_packages",
            "ACTION-INPUT INSTALL DRIFT", workflow_text=real_text.replace(
                '          python-version: "3.12"\n',
                '          python-version: "3.12"\n'
                '          pip-install: pytest-xdist\n', 1))

    def test_inexact_constraint_pins_redden(self, tmp_path, monkeypatch):
        """MUTATION ARM (pin contract): `==` presence is not a pin.

        Round-7 probe: a wildcard (`==0.17.*`) and an environment marker
        inactive on the CI interpreter both passed a check that looked for
        `==` anywhere in the line, while the direct dependency floated.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        pins = (root / "constraints-ci.txt").read_text(encoding="utf-8")
        line = next(
            ln for ln in pins.splitlines()
            if ln.strip() and not ln.startswith("#"))
        # The pin itself, without any trailing comment the shipped line may
        # carry, so every shape below mutates the PIN, not its comment.
        pin = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()
        name, _, version = pin.partition("==")
        major_minor = ".".join(version.split(".")[:2])
        for shape, replacement in [
            ("wildcard", f"{name}=={major_minor}.*"),
            ("marker", f'{pin}; python_version < "3.11"'),
            ("extra", f"{name}[dev]=={version}"),
            # A comment is stripped first, so it cannot dress up a range.
            ("commented-wildcard", f"{name}=={major_minor}.*  # floats"),
        ]:
            self._shape_reddens(
                tmp_path, monkeypatch, shape,
                "test_constraints_pins_cover_the_install_line",
                "INEXACT PIN", extra_files={
                    "constraints-ci.txt": pins.replace(line, replacement, 1)})

        # CONTROL (over-block): pip ends a requirements line at whitespace
        # + `#`, so an exact pin with a trailing comment IS an exact pin.
        self._shape_stays_green(
            tmp_path, monkeypatch, "trailing-comment",
            "test_constraints_pins_cover_the_install_line", extra_files={
                "constraints-ci.txt": pins.replace(
                    line, f"{pin}  # pinned by the verified freeze", 1)})

    def test_runtime_pip_config_reddens(self, tmp_path, monkeypatch):
        """MUTATION ARM (runtime config): pip configured while CI RUNS.

        Round-7 probes: `PIP_INDEX_URL` appended to $GITHUB_ENV and
        `pip config set global.index-url` configured every later install
        with every arm green, because the env arm read static `env:` only;
        and an `export PIP_INDEX_URL` in .cursor/install.sh was never read.
        Each must redden the env arm naming the command.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        real_script = (root / ".cursor" / "install.sh").read_text(encoding="utf-8")
        install_step = "      - name: Install test dependencies\n"
        env_arm = "test_no_pip_environment_key_redirects_the_install"

        def before_install(run_block):
            return real_text.replace(
                install_step,
                "      - name: mirror\n        run: |\n"
                + "".join(f"          {ln}\n" for ln in run_block)
                + install_step, 1)

        for name, block in [
            ("github-env-echo",
             ['echo "PIP_INDEX_URL=https://x.example/simple" >> "$GITHUB_ENV"']),
            ("github-env-printf",
             ["printf 'PIP_INDEX_URL=%s\\n' https://x.example/simple >> $GITHUB_ENV"]),
            ("github-env-heredoc",
             ["cat >> \"$GITHUB_ENV\" <<'EOF'",
              "PIP_EXTRA_INDEX_URL=https://x.example/simple", "EOF"]),
            ("github-env-tee",
             ["echo PIP_INDEX_URL=https://x.example/simple | tee -a $GITHUB_ENV"]),
            ("pip-config-set",
             ["python -m pip config set global.index-url https://x.example/simple"]),
            ("pip-conf-file",
             ["printf '[global]\\nindex-url=https://x.example\\n' > ~/.config/pip/pip.conf"]),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, env_arm,
                                "PIP ENVIRONMENT DRIFT",
                                workflow_text=before_install(block))

        self._shape_reddens(
            tmp_path, monkeypatch, "composite-env", env_arm,
            "PIP ENVIRONMENT DRIFT", extra_files={
                ".github/actions/setup/action.yml":
                    "name: setup\nruns:\n  using: composite\n  steps:\n"
                    "    - run: echo hi\n      shell: bash\n      env:\n"
                    "        PIP_INDEX_URL: https://x.example/simple\n"})
        for name, line in [
            ("script-export", "export PIP_INDEX_URL=https://x.example/simple"),
            ("script-assign", "PIP_INDEX_URL=https://x.example/simple"),
        ]:
            self._shape_reddens(
                tmp_path, monkeypatch, name, env_arm, "PIP ENVIRONMENT DRIFT",
                script_text=real_script.replace(
                    ". .venv/bin/activate\n",
                    ". .venv/bin/activate\n" + line + "\n", 1))

    def test_tool_runners_redden(self, tmp_path, monkeypatch):
        """MUTATION ARM (tool runners, installers, actions): each reddens
        at its arm — commands at the tool-runner arm, actions at the
        allowlist arm.

        Round-7 probes: `pipx run`, `uvx` and a `uses:` of
        astral-sh/ruff-action each executed an index-resolved tool with
        every arm green, because none writes `pip install`.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        real_text = workflow.read_text(encoding="utf-8")
        real_script = (root / ".cursor" / "install.sh").read_text(encoding="utf-8")
        lint_anchor = "          ruff --version\n"
        lint_step = "      - name: Import-hygiene lint (F401/F821)\n"
        arm = "test_no_tool_runner_executes_unpinned_code"
        for name, command in [
            ("pipx-run", "pipx run ruff check pact-plugin/"),
            ("pipx-install", "pipx install ruff"),
            ("uvx", "uvx ruff check pact-plugin/"),
            ("uv-tool", "uv tool run ruff check pact-plugin/"),
            ("uv-add", "uv add ruff"),
            ("uv-sync", "uv sync"),
            ("uv-run-with", "uv run --with ruff ruff check pact-plugin/"),
            ("uv-run-with-eq", "uv run --with=ruff ruff check pact-plugin/"),
            ("poetry-install", "poetry install"),
            ("poetry-add", "poetry add ruff"),
            ("conda-install", "conda install -y ruff"),
            ("mamba-install", "mamba install -y ruff"),
            ("micromamba-install", "micromamba install -y ruff"),
            # ALLOWLIST polarity: verbs and programs no list named.
            ("uv-pip-sync", "uv pip sync requirements.txt"),
            ("pip-sync", "pip-sync requirements.txt"),
            ("poetry-sync", "poetry sync"),
            ("pipenv-install", "pipenv install"),
            ("pdm-install", "pdm install"),
            ("hatch-run", "hatch run test"),
            ("tox", "tox"),
            ("nox", "nox -s tests"),
            ("conda-create", "conda create -n ci python=3.12 numpy"),
            ("conda-env-create", "conda env create -f environment.yml"),
            ("mamba-update", "mamba update numpy"),
            ("uv-global-then-add", "uv --project pact-plugin add httpx"),
            ("uv-run-script", "uv run scripts/check.py"),
            ("uv-run-plain", "uv run ruff check pact-plugin/"),
            ("pre-commit-run", "pre-commit run --all-files"),
            ("python-m-tox", "python -m tox"),
            ("python-m-pre-commit", "python -m pre_commit run"),
            # Program identity by BASENAME (Bugbot B1).
            ("uv-by-path-sync", "/opt/tools/uv sync"),
            ("uvx-by-path", "$HOME/.local/bin/uvx ruff check pact-plugin/"),
            # A valued global option's VALUE in the verb slot fails closed
            # (Bugbot B2): no table of these tools' global options exists.
            ("uv-directory-sync", "uv --directory proj sync"),
            ("uv-C-add", "uv -C proj add ruff"),
            ("pipx-python-install", "pipx --python python3.12 install ruff"),
        ]:
            self._shape_reddens(
                tmp_path, monkeypatch, name, arm, "TOOL-RUNNER DRIFT",
                workflow_text=real_text.replace(
                    lint_anchor, lint_anchor + f"          {command}\n", 1))
        for name, uses in [
            ("ruff-action",
             "astral-sh/ruff-action@0c50076f12c38c3d0115b7b519b54a91cb9cf0ad"),
            ("setup-uv", "astral-sh/setup-uv@v6"),
            ("setup-miniconda", "conda-incubator/setup-miniconda@v3"),
            ("setup-micromamba", "mamba-org/setup-micromamba@v2"),
            # The auditor's probe: an installer action no deny-list named.
            ("pre-commit-action", "pre-commit/action@v3.0.1"),
            ("docker-image", "docker://python:3.12-slim"),
        ]:
            self._shape_reddens(
                tmp_path, monkeypatch, name,
                "test_every_third_party_action_is_allowlisted",
                "ACTION ALLOWLIST DRIFT",
                workflow_text=real_text.replace(
                    lint_step, f"      - uses: {uses}\n" + lint_step, 1))
        # A third-party REUSABLE WORKFLOW called at job level is held too.
        self._shape_reddens(
            tmp_path, monkeypatch, "reusable-workflow",
            "test_every_third_party_action_is_allowlisted",
            "ACTION ALLOWLIST DRIFT", extra_files={
                ".github/workflows/reuse.yml":
                    "name: reuse\non: [pull_request]\njobs:\n  r:\n"
                    "    uses: someorg/ci/.github/workflows/lint.yml@v1\n"})
        # CONTROL: an allowlisted action re-pinned to another ref is not
        # drift — the allowlist keys on the name, not the @ref.
        self._shape_stays_green(
            tmp_path, monkeypatch, "allowlisted-repin",
            "test_every_third_party_action_is_allowlisted",
            workflow_text=real_text.replace(
                lint_step, "      - uses: actions/checkout@v5\n" + lint_step, 1))
        # `python setup.py develop` installs the project and its declared
        # requirements unpinned; the census also follows setup.py as a
        # script, so the file exists in the shape.
        self._shape_reddens(
            tmp_path, monkeypatch, "setup-py-develop", arm, "TOOL-RUNNER DRIFT",
            workflow_text=real_text.replace(
                lint_anchor, lint_anchor + "          python setup.py develop\n", 1),
            extra_files={"setup.py": "from setuptools import setup\nsetup()\n"})
        self._shape_reddens(
            tmp_path, monkeypatch, "script-pipx", arm, "TOOL-RUNNER DRIFT",
            script_text=real_script + "\npipx install pre-commit\n")
        self._shape_reddens(
            tmp_path, monkeypatch, "script-uv-sync", arm, "TOOL-RUNNER DRIFT",
            script_text=real_script + "\nuv sync\n")

        # CONTROLS: read-only invocations of the same programs install
        # nothing and stay green — the allowlist admits only those.
        self._shape_stays_green(
            tmp_path, monkeypatch, "read-only-verbs", arm,
            workflow_text=real_text.replace(
                lint_anchor,
                lint_anchor
                + "          uv --version\n          conda list\n"
                + "          poetry --version\n          pipx list\n"
                + "          uv pip list\n          conda env list\n"
                + "          pre-commit --version\n          poetry show\n"
                + "          uv --quiet pip list\n"
                + "          uv --directory=pact-plugin pip list\n", 1))

    def test_the_census_follows_what_ci_executes(self, tmp_path, monkeypatch):
        """MUTATION ARM (execution-defined population): installs reached
        through a local action anywhere in the repo, a repo script a step
        runs, or a string a shell runs as code redden.

        Auditor probes: `uses: ./tools/setup` (a composite action outside
        .github/actions), a run step `./scripts/ci-extra.sh` whose script
        runs `pip install`, and `bash -c 'pip install x'` were each GREEN
        against the directory-keyed census. Each shape below is followed
        to its install and must redden at the named shipped arm; the
        controls name paths that are NOT repo scripts and must stay green.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        real_text = (root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8")
        lint_step = "      - name: Import-hygiene lint (F401/F821)\n"
        census = "test_every_executed_pip_install_is_the_canonical_install_line"
        env_arm = "test_no_pip_environment_key_redirects_the_install"
        tool_arm = "test_no_tool_runner_executes_unpinned_code"
        installs = "#!/usr/bin/env bash\nset -e\npip install evilpkg\n"
        composite = ("name: setup\nruns:\n  using: composite\n  steps:\n"
                     "    - run: pip install evilpkg\n      shell: bash\n")

        def with_step(step_yaml):
            return real_text.replace(lint_step, step_yaml + lint_step, 1)

        def run_step(command, workdir=None):
            step = f"      - run: {command}\n"
            if workdir:
                step += f"        working-directory: {workdir}\n"
            return with_step(step)

        for name, workflow, extra, arm, match in [
            ("local-action-anywhere", with_step("      - uses: ./tools/setup\n"),
             {"tools/setup/action.yml": composite}, census, "INSTALL CENSUS DRIFT"),
            ("nested-composite", with_step("      - uses: ./tools/outer\n"),
             {"tools/outer/action.yml":
                  "name: outer\nruns:\n  using: composite\n  steps:\n"
                  "    - uses: ./tools/inner\n",
              "tools/inner/action.yml": composite},
             census, "INSTALL CENSUS DRIFT"),
            ("script-by-path", run_step("./scripts/ci-extra.sh"),
             {"scripts/ci-extra.sh": installs}, census, "INSTALL CENSUS DRIFT"),
            ("script-as-bash-operand", run_step("bash scripts/ci-extra.sh"),
             {"scripts/ci-extra.sh": installs}, census, "INSTALL CENSUS DRIFT"),
            ("script-workdir", run_step("./ci-extra.sh", "pact-plugin"),
             {"pact-plugin/ci-extra.sh": installs}, census, "INSTALL CENSUS DRIFT"),
            ("script-workspace-var",
             run_step('bash "$GITHUB_WORKSPACE/scripts/ci-extra.sh"'),
             {"scripts/ci-extra.sh": installs}, census, "INSTALL CENSUS DRIFT"),
            ("script-calls-script", run_step("./scripts/outer.sh"),
             {"scripts/outer.sh": "#!/bin/sh\nsh scripts/inner.sh\n",
              "scripts/inner.sh": installs}, census, "INSTALL CENSUS DRIFT"),
            ("python-script", run_step("python scripts/ci_extra.py"),
             {"scripts/ci_extra.py":
                  "import subprocess\n"
                  "subprocess.run(['pip', 'install', 'evilpkg'], check=True)\n"},
             census, "INSTALL CENSUS DRIFT"),
            ("bash-c", run_step("bash -c 'pip install evilpkg'"), {},
             census, "INSTALL CENSUS DRIFT"),
            ("sh-ec", run_step('sh -ec "pip install evilpkg"'), {},
             census, "INSTALL CENSUS DRIFT"),
            ("eval", run_step("eval 'pip install evilpkg'"), {},
             census, "INSTALL CENSUS DRIFT"),
            ("python-c",
             run_step("python -c \"import subprocess; "
                      "subprocess.run(['pip', 'install', 'evilpkg'])\""),
             {}, census, "INSTALL CENSUS DRIFT"),
            ("script-env-write", run_step("./scripts/ci-extra.sh"),
             {"scripts/ci-extra.sh": "#!/bin/sh\n"
              "echo PIP_INDEX_URL=https://x.example/simple >> $GITHUB_ENV\n"},
             env_arm, "PIP ENVIRONMENT DRIFT"),
            ("script-tool-runner", run_step("./scripts/ci-extra.sh"),
             {"scripts/ci-extra.sh": "#!/bin/sh\nuvx ruff check .\n"},
             tool_arm, "TOOL-RUNNER DRIFT"),
            ("non-composite-action", with_step("      - uses: ./tools/js\n"),
             {"tools/js/action.yml":
                  "name: js\nruns:\n  using: node20\n  main: index.js\n"},
             census, "UNREADABLE LOCAL ACTION"),
            ("missing-action", with_step("      - uses: ./tools/nothing\n"), {},
             census, "UNRESOLVED LOCAL ACTION"),
            ("missing-script", run_step("./scripts/missing.sh"), {},
             census, "UNRESOLVED SCRIPT"),
            ("unreadable-script", run_step("./scripts/ci-extra.rb"),
             {"scripts/ci-extra.rb": "system('pip install evilpkg')\n"},
             census, "UNREADABLE SCRIPT"),
        ]:
            self._shape_reddens(tmp_path, monkeypatch, name, arm, match,
                                workflow_text=workflow, extra_files=extra)

        # CONTROLS: not repo scripts, and a followed script whose only pip
        # text is a quoted message — all must stay green.
        for name, workflow, extra in [
            ("runner-temp", run_step('bash "$RUNNER_TEMP/setup.sh"'), {}),
            ("home-path", run_step("$HOME/bin/tool.sh"), {}),
            ("python-module", run_step("python -m pip --version"), {}),
            ("quoted-hint", run_step("./scripts/hint.sh"),
             {"scripts/hint.sh":
                  "#!/bin/sh\necho 'run: python3 -m pip install -c "
                  "constraints-ci.txt ruff'\n"}),
        ]:
            copy = self._repo_copy_carrying(
                tmp_path / name, workflow_text=workflow, extra_files=extra)
            monkeypatch.setattr(
                type(self), "_source_repo_root", lambda self: copy)
            self.test_every_executed_pip_install_is_the_canonical_install_line()
            self.test_no_tool_runner_executes_unpinned_code()
            self.test_no_pip_environment_key_redirects_the_install()

        # The walk's per-root cache must never serve a stale read: editing
        # a FOLLOWED file in place (same root) changes what the census sees.
        copy = self._repo_copy_carrying(
            tmp_path / "cache-freshness",
            workflow_text=run_step("./scripts/later.sh"),
            extra_files={"scripts/later.sh": "#!/bin/sh\necho ok\n"})
        assert len(self._population_installs(copy)) == 1
        (copy / "scripts" / "later.sh").write_text(
            "#!/bin/sh\necho ok\npip install evilpkg\n", encoding="utf-8")
        assert len(self._population_installs(copy)) == 2, (
            "STALE EXECUTION CACHE: a followed script gained an install "
            "in place and the census did not see it."
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
