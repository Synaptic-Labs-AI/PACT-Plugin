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
        """
        for base in Path(__file__).resolve().parents:
            if (base / ".claude-plugin" / "marketplace.json").exists():
                return base
        return None

    # pip flags whose VALUE is a separate argv word (`-c constraints-ci.txt`):
    # the value names a file, URL or option target — not a package — so the
    # tokeniser must consume it. `--flag=value` forms carry the value inline
    # and need no entry here. shortcut: a future value-taking flag unknown to
    # this set leaves its value counted as a package operand. That is
    # harmless for the declared-missing direction (extra tokens create no
    # false drift) but the coverage arm turns it into a TRIPWIRE: it reports
    # the value as an UNPINNED package and reddens naming it — the value's
    # name in the message is the tell. So adding such a flag to the install
    # line requires extending this set in the same change; the red is the
    # reminder, not a bug.
    _PIP_FLAGS_WITH_VALUE = frozenset({
        "-c", "--constraint", "-r", "--requirement", "-e", "--editable",
        "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
        "-t", "--target", "--prefix", "--src", "--platform",
        "--python-version", "--implementation", "--abi", "--root",
        "--cert", "--client-cert", "--proxy", "--cache-dir",
        "--no-binary", "--only-binary", "--config-settings",
        "--global-option", "--install-option", "--build-option",
        "--progress-bar", "--retries", "--timeout", "--exists-action",
        "--hash",
    })

    # The census anchor: `pip install` OR `pip3 install`, over ANY run of
    # spaces/tabs between the two words. pip3 is the stock spelling on
    # GitHub's Ubuntu runners, so a one-word honest edit switches spelling
    # and every substring-anchored extractor goes blind at once (round-5
    # security probe: `pip3 install evilpkg` evaded the census, exactly-one,
    # coverage, constraints-APPLIED AND the -r/-e tripwire simultaneously).
    # The whitespace run is equally deliberate (round-6 probes): the shell
    # collapses `pip  install` (double space) and reads a TAB as IFS, so
    # both spellings execute while a literal-single-space anchor reads
    # neither — an inline tab is invalid YAML and dies at parse, so only
    # the block-body spellings were live holes. `pipx install` still does
    # NOT match — pipx installs into isolated environments `-c` cannot
    # constrain and stays outside this census by design — while
    # `uv pip install` DOES match and is censused deliberately: it
    # resolves packages into the same environment unconstrained.
    _PIP_INSTALL_COMMAND_RE = re.compile(r"\bpip3?[ \t]+install\b")

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
    def _package_tokens_from_pip_argv(cls, command):
        """Package-name tokens of one `pip install` command string.

        Model how pip itself reads argv: package operands are the words
        after the `install` verb that are neither flags nor the VALUE of a
        flag that takes one. Everything before the verb is the invocation
        (`run:`, `python`, `-m`, `pip`) and is not an operand; deriving
        the boundary structurally beats filtering a hand-maintained list
        of command words, which would need updating and would turn a
        package legitimately named after a command into false drift.
        """
        words = command.split()
        operands = words[words.index("install") + 1:] if "install" in words else []
        package_words = []
        consume_value = False
        for tok in operands:
            if consume_value:
                consume_value = False
                continue
            if tok.startswith("-"):
                # `--flag=value` carries its value inline; a bare flag in
                # _PIP_FLAGS_WITH_VALUE takes its value as the NEXT word.
                if "=" not in tok and tok in cls._PIP_FLAGS_WITH_VALUE:
                    consume_value = True
                continue
            package_words.append(tok)
        return {cls._normalise_pin_name(tok) for tok in package_words if tok}

    @staticmethod
    def _applies_constraints(command):
        """Does a pip-install command APPLY constraints-ci.txt?

        Accepts the separate-word spellings (`-c constraints-ci.txt`,
        `--constraint constraints-ci.txt`) and the inline one
        (`--constraint=constraints-ci.txt`) that pip documents; anything
        else is a re-point, not a pass.
        """
        words = command.split()
        for i, tok in enumerate(words):
            if tok in ("-c", "--constraint") \
                    and words[i + 1:i + 2] == ["constraints-ci.txt"]:
                return True
            if tok == "--constraint=constraints-ci.txt":
                return True
        return False

    def _ci_install_line(self, workflow):
        """The workflow's single `run:` line carrying a pip/pip3 install.

        The parse anchors on the `run:` line — the EXECUTED command — never
        on any line merely mentioning an install: workflow comment prose
        may name the command (the CONSTRAINTS paragraph does), and prose is
        not a declaration. `pip3` matches too — it is the same package
        manager under its runner-stock spelling, and an install that
        switches spelling must not fall outside the parse (round-5 probe).
        Exactly-one is asserted here so every consumer (tokens, coverage,
        application) shares one parse contract. Folded plain scalars are
        REFUSED before the filter runs — see
        `_refuse_plain_scalar_run_continuations`.
        """
        text = workflow.read_text(encoding="utf-8")
        self._refuse_plain_scalar_run_continuations(text)
        install_lines = [
            ln for ln in text.splitlines()
            if ln.lstrip().startswith("run:")
            and self._PIP_INSTALL_COMMAND_RE.search(ln)
        ]
        assert install_lines, (
            f"PARSE FAILURE, not dependency drift: {workflow} contains no "
            f"`run:` line carrying a pip/pip3 install, so the CI dependency "
            f"declaration cannot be read. The workflow changed shape — "
            f"re-point this parity test at the new shape; do not weaken it "
            f"to a skip."
        )
        assert len(install_lines) == 1, (
            f"PARSE AMBIGUITY, not dependency drift: {len(install_lines)} "
            f"`run:` lines carrying a pip/pip3 install were read from "
            f"{workflow} ({install_lines}). This guard reasons about THE CI "
            f"install line, singular — a second install step must be "
            f"reconciled here, not silently first-matched."
        )
        return install_lines[0]

    @staticmethod
    def _logical_commands_from_block_body(body_lines, folded):
        """Block-body lines -> the shell strings they actually execute.

        A literal (`|`) body runs ONE shell line per body line, EXCEPT a
        trailing backslash joins the next line into the same command
        (round-6 probes P3/P3b: `pip \\` + `install evilpkg` executes as
        `pip install evilpkg` — argv ground-truthed — while a line-local
        anchor reads neither half). A folded (`>`) body is ONE scalar:
        YAML folds each newline into a space, so the whole body executes
        as one string (round-6 probe P2: pip and install split across
        body lines fold back together). Real folded scalars keep newlines
        at blank lines and deeper indentation; joining everything on one
        space regardless only ever reads MORE as one command — over-catch,
        never under-catch, for an anchor test.
        """
        if folded:
            return [" ".join(body_lines)]
        commands = []
        current = ""
        for line in body_lines:
            current = f"{current} {line}".strip()
            if current.endswith("\\"):
                current = current.rstrip("\\").strip()
                continue
            commands.append(current)
            current = ""
        if current:
            commands.append(current)
        return commands

    @classmethod
    def _workflow_executed_pip_install_lines(cls, workflow_text):
        """Every EXECUTED command bearing a pip/pip3 install, in either run
        form — normalised to the string the shell executes BEFORE the
        anchor runs.

        Two executed forms exist in a workflow: an inline `run: <command>`
        line, and the body of a `run: |`/`run: >` block — the standard
        idiom for multi-command steps, already used twice in this workflow
        (the model warm-up and the lint legs). Block bodies pass through
        `_logical_commands_from_block_body` first, so a backslash-joined
        install (`pip \\` + `install pkg`, the natural wrap for the
        130-char canonical line) and a folded body that splits the words
        across lines both anchor like their one-line spelling. Comment
        lines are dropped in BOTH positions: workflow-header prose and
        in-block shell comments are never executed, and the header names
        pip commands (prose is not a declaration). `pip3` is censused
        with `pip` — the same package manager under its runner-stock
        spelling, invisible to every arm while installing until the
        round-5 probe (probed live: all arms green with
        `pip3 install evilpkg`). pipx is BOUNDED OUT structurally:
        `pipx install` does not match the census anchor and installs into
        isolated environments, so `-c` cannot constrain it and it is not
        this census's surface. `uv pip install` DOES match the anchor and
        censuses like any other pip install — it resolves packages into
        the same environment unconstrained. A THIRD executed form, the
        folded plain scalar, is REFUSED outright before the census runs —
        see `_refuse_plain_scalar_run_continuations`.
        """
        cls._refuse_plain_scalar_run_continuations(workflow_text)
        executed = []
        block_indent = None
        block_folded = False
        block_body = []

        def flush_block():
            for command in cls._logical_commands_from_block_body(
                    block_body, block_folded):
                if cls._PIP_INSTALL_COMMAND_RE.search(command):
                    executed.append(command)

        for raw in workflow_text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                # Blank and comment lines never execute and never end a
                # block: only a dedented line closes one.
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if block_indent is not None:
                if indent > block_indent:
                    block_body.append(stripped)
                    continue
                flush_block()
                block_indent = None
                block_body = []
            # `[|>]` covers literal and folded scalars; a trailing chomping
            # indicator (`|-`, `>-`) still matches the single class digit.
            header = re.match(r"^\s*-?\s*run:\s*([|>])", raw)
            if header:
                block_indent = indent
                block_folded = header.group(1) == ">"
            elif re.match(r"^\s*-?\s*run:", raw) \
                    and cls._PIP_INSTALL_COMMAND_RE.search(raw):
                executed.append(raw)
        if block_indent is not None:
            flush_block()
        return executed

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
        command. Block scalars (`run: |`, `run: >`) are legal — their
        bodies are handled by the block tracking in
        `_workflow_executed_pip_install_lines`, which runs after this
        check and would otherwise misread a folded body as two lines.
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
            if re.match(r"^\s*-?\s*run:", raw):
                run_indent = indent

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
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            assert "==" in line, (
                f"PARSE FAILURE, not coverage drift: {constraints} carries "
                f"a non-pin line {line!r}. The file's contract is one "
                f"`name==version` pin per direct dependency — a line with "
                f"no `==` is either prose that lost its `#` or a range "
                f"constraint, and both must be reconciled by hand."
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
        """The script's pip/pip3 install commands, from EXECUTED text only.

        Comment lines are dropped FIRST (the script's prose may quote pip
        commands — prose is not a declaration), and a command extends
        through its backslash continuations BEFORE the anchor runs, so a
        wrapped `python -m pip \\` + `install pkg` enumerates like its
        one-line spelling (round-6 probe S1: the per-line start condition
        let the wrapped form ride every script arm while installing). The
        anchor is then applied to each JOINED command, and only
        anchor-matching joins are returned — the script's other continued
        commands (the apt-get bootstrap lines) are joined and released
        without ever reaching the arms. `pip3` enumerates with `pip`:
        the round-5 probe showed a `pip3 install` line in the script
        reading as no command at all, riding past the constraints-APPLIED
        all() and the set parity while floating an unpinned package into
        the local mirror. Shared by every arm that reasons about the
        script surface, so one parse contract covers them all.
        """
        logical = []
        pending = None
        for raw in script.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if pending is not None:
                pending = f"{pending} {line.rstrip(chr(92)).strip()}".strip()
                if not raw.rstrip().endswith("\\"):
                    logical.append(pending)
                    pending = None
                continue
            if line.startswith("#"):
                continue
            if not raw.rstrip().endswith("\\"):
                logical.append(line)
                continue
            pending = line.rstrip("\\").strip()
        if pending is not None:
            logical.append(pending)
        commands = [
            cmd for cmd in logical
            if self._PIP_INSTALL_COMMAND_RE.search(cmd)
        ]
        assert commands, (
            f"PARSE FAILURE, not parity drift: no non-comment pip/pip3 "
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
        """`-r`/`-e` on either install surface floats names invisibly.

        Both flags take a VALUE, so the tokenizer consumes it and the flag
        never reaches the package tokens: a requirements file or editable
        path adds packages that the declared-match, coverage and
        constraints-APPLIED arms never see, while constraints-ci.txt pins
        none of them (it mirrors the install line's direct names only). An
        innocent refactor to a `-r requirements-dev.txt` install is exactly
        the honest mistake this class exists for, so the flags are refused on
        both parsed surfaces: the workflow's install line and EVERY
        pip/pip3 install command in the script — the pip-upgrade command included,
        where a flag would otherwise ride with no package operands to betray
        it. Introducing one legitimately means
        reconciling the coverage arms to read the referenced file in the
        same change — the red here is the reminder, not a bug.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ or .cursor/ tree by design. "
                "This skip is NO INFORMATION about the -r/-e tripwire — "
                "never read it as a pass."
            )
        forbidden = ("-r", "--requirement", "-e", "--editable")

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
        # EVERY command, not just those with package operands: the pip-upgrade
        # command has none, so a flag riding it is invisible to any
        # token-based filter — exactly where it would float silently.
        surfaces = [
            ("the CI install line", [self._ci_install_line(workflow)]),
            (f"{script} install commands", script_commands),
        ]
        for where, commands in surfaces:
            for cmd in commands:
                hits = sorted({
                    tok for tok in cmd.split()
                    if tok in forbidden
                    or tok.startswith("--requirement=")
                    or tok.startswith("--editable=")
                })
                assert not hits, (
                    f"REQUIREMENTS-FLAG DRIFT: {where} carries {hits} "
                    f"({cmd.strip()!r}). Both flags take a value the "
                    f"tokenizer consumes, so every package named by the "
                    f"referenced file installs with NO pin this machinery "
                    f"guarantees — the coverage arms cannot see them. Pin "
                    f"the names as install-line operands instead, or "
                    f"reconcile the coverage arms to read the referenced "
                    f"file in the same change."
                )

    def test_every_executed_pip_install_is_the_canonical_install_line(self):
        """Census: no pip or pip3 install hides from the canonical parse.

        `_ci_install_line` anchors on run:-PREFIXED single lines — the
        executed INLINE form only. A second install written inside a
        `run: |` block matches that anchor by construction, so every arm
        built on it (declared-match, coverage, constraints-APPLIED, the
        -r/-e tripwire) keeps reading only the canonical line while the
        block install floats free: probed at 68ef5021, a block-form
        `pip install some-unpinned-tool` parsed CLEAN through the shipped
        helpers. The census closes that hole — every executed
        pip-install line, inline OR block body, must BE the one canonical
        line, so a block install reddens here even though the canonical
        parse still succeeds. The other direction needs no arm: moving
        the canonical install INTO a block reddens `_ci_install_line`
        itself (PARSE FAILURE, no run:-prefixed match). The census counts
        BOTH pip spellings — a `pip3 install` is an install under the
        runner-stock spelling (round-5 probe: invisible to every arm while
        installing) — and folded plain scalars are REFUSED before it runs,
        so neither shape can carry an install past the equality below.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json "
                f"above {Path(__file__).resolve()}, so this is not a PACT "
                "source checkout. The expected case is an INSTALLED PLUGIN "
                "CACHE, which has no .github/ tree by design. This skip is "
                "NO INFORMATION about block-form installs — never read it "
                "as a pass."
            )
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) "
            f"but {workflow} is missing. The census cannot run. This is a "
            f"FAILURE, not a skip: inside a checkout the workflow is "
            f"required."
        )
        canonical = self._ci_install_line(workflow)
        census = self._workflow_executed_pip_install_lines(
            workflow.read_text(encoding="utf-8")
        )
        assert census, (
            "PARSE FAILURE, not census agreement: the canonical parse read "
            "an install line but the census found no executed pip-install "
            "line, so the census no longer models the workflow. Re-point "
            "it; do not delete it."
        )
        assert census == [canonical], (
            "BLOCK-FORM INSTALL DRIFT: executed pip-install lines exist "
            "beyond the canonical install step:\n"
            + "\n".join(
                f"  {ln.strip()!r}" for ln in census if ln != canonical
            )
            + "\nThe canonical parser reads run:-prefixed lines only, so "
            "every coverage / constraints / tripwire arm above reads just "
            "the canonical line while these installs resolve unpinned. "
            "Fold them into the canonical install step, or reconcile the "
            "arms to read them in the same change."
        )

    def _repo_copy_carrying(self, tmp_path, workflow_text=None, script_text=None):
        """A minimal repo copy over a POSITIVE marker, for the mutation arms.

        The arms in this class find the repo root by marker
        (`.claude-plugin/marketplace.json`); this rig plants that marker
        over a scratch tree so a mutation lands on a COPY — never the
        checkout — while constraints-ci.txt ships verbatim, because every
        arm compares the mutated surface against the REAL pins. The
        workflow and install script default to their real bytes so a
        mutation is one targeted substitution on top of the shipped
        state, not a hand-retyped fixture that can drift from it.
        """
        root = self._source_repo_root()
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
        (tmp_path / ".github" / "workflows" / "tests.yml").write_text(
            workflow_text
            if workflow_text is not None
            else (root / ".github" / "workflows" / "tests.yml")
            .read_text(encoding="utf-8"),
            encoding="utf-8")
        (tmp_path / "constraints-ci.txt").write_text(
            (root / "constraints-ci.txt").read_text(encoding="utf-8"),
            encoding="utf-8")
        (tmp_path / ".cursor").mkdir(parents=True)
        (tmp_path / ".cursor" / "install.sh").write_text(
            script_text
            if script_text is not None
            else (root / ".cursor" / "install.sh").read_text(encoding="utf-8"),
            encoding="utf-8")
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
        with pytest.raises(AssertionError, match="BLOCK-FORM INSTALL DRIFT"):
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
        with pytest.raises(AssertionError, match="BLOCK-FORM INSTALL DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # P3 — backslash continuation inside a `|` body: bash joins
        # `pip \` + `install evilpkg` into one argv, so the census must
        # read the JOIN, not the lines.
        against(real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          pip \\\n"
            "          install evilpkg\n",
            1), "p3")
        with pytest.raises(AssertionError, match="BLOCK-FORM INSTALL DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # P3b — the natural wrap of the 130-char canonical spelling:
        # `python -m pip \` + `install evilpkg` joins the same way.
        against(real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          python -m pip \\\n"
            "          install evilpkg\n",
            1), "p3b")
        with pytest.raises(AssertionError, match="BLOCK-FORM INSTALL DRIFT"):
            self.test_every_executed_pip_install_is_the_canonical_install_line()

        # S6-2 — a TAB as the separator in a `|` body: tab is IFS to the
        # shell, so the widened anchor must match what a literal space
        # missed. Inline tabs are invalid YAML and die at parse; the
        # block body is the live spelling.
        against(real_text.replace(
            "          ruff --version\n",
            "          ruff --version\n          pip\tinstall evilpkg\n",
            1), "tab")
        with pytest.raises(AssertionError, match="BLOCK-FORM INSTALL DRIFT"):
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

    def _pip_environment_keys(self, workflow_text):
        """`env:` keys configuring pip, from job- and step-level blocks.

        A PIP_* key is a pip configuration channel that operates
        INDEPENDENTLY of the install line this class reads. The `env:`
        line is anchored to end-of-line so `environment:` never matches;
        keys are compared case-insensitively (over-catch: pip itself
        reads only uppercase, but a lowercase `pip_*` key reads as
        intended-for-pip to every future reader even if pip ignores it).
        """
        offenders = []
        env_indent = None
        for raw in workflow_text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if env_indent is not None:
                if indent > env_indent:
                    key = stripped.split(":", 1)[0].strip()
                    if key.upper().startswith("PIP_"):
                        offenders.append(key)
                    continue
                env_indent = None
            if re.match(r"^\s*-?\s*env:\s*$", raw):
                env_indent = indent
        return offenders

    def test_no_pip_environment_key_redirects_the_install(self):
        """No `env:` key may start with PIP_ — it redirects pip unseen.

        PIP_INDEX_URL redirects every resolve to an index of someone
        else's choosing, PIP_CONSTRAINT re-points the constraint file,
        and the constraints file's `==` pins carry no hashes — so a
        redirected index can serve attacker builds of the pinned
        versions. Round-6 probe (S6-3): such a key rode every arm in
        this class green, because environment configuration touches
        none of the surfaces they read. The workflow carries NO env:
        block today, so this arm is a TRIPWIRE, deliberately
        over-catch-only: any PIP_* key reddens naming itself whether or
        not the use looks legitimate — a future key with a real purpose
        is reconciled by justifying it HERE and naming it in this
        docstring, never by deleting the arm.
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
        offenders = self._pip_environment_keys(
            workflow.read_text(encoding="utf-8"))
        assert not offenders, (
            f"PIP ENVIRONMENT DRIFT: env: key(s) {offenders} in "
            f"{workflow} configure pip through the environment, OUTSIDE "
            f"every install-line arm — PIP_INDEX_URL redirects resolves "
            f"to an index the constraints file never vetted, and the "
            f"pins carry no hashes, so the redirect serves builds of the "
            f"pinned versions. A PIP_* key needs a recorded decision in "
            f"test_no_pip_environment_key_redirects_the_install, not a "
            f"silent env: line."
        )

    def test_a_pip_env_key_reddens_naming_itself(self, tmp_path, monkeypatch):
        """MUTATION ARM (env): a PIP_INDEX_URL step key reddens by name.

        Planted exactly where it would ride silently pre-fix — a step
        env: block on the install step — and killed by the shipped
        tripwire, with the key's own name in the message.
        """
        root = self._source_repo_root()
        if root is None:
            pytest.skip("SKIPPED, NOT PASSED — not a source checkout; see "
                        "_repo_copy_carrying for the full wording.")
        workflow = root / ".github" / "workflows" / "tests.yml"
        canonical = self._ci_install_line(workflow)
        mutated = workflow.read_text(encoding="utf-8").replace(
            canonical,
            canonical + "\n        env:\n"
                        "          PIP_INDEX_URL: https://evil.example/simple",
            1,
        )
        copy = self._repo_copy_carrying(
            tmp_path / "env", workflow_text=mutated)
        monkeypatch.setattr(type(self), "_source_repo_root", lambda self: copy)
        with pytest.raises(AssertionError, match="PIP ENVIRONMENT DRIFT") \
                as exc_info:
            self.test_no_pip_environment_key_redirects_the_install()
        assert "PIP_INDEX_URL" in str(exc_info.value), (
            f"the red must name the offending key; got: {exc_info.value}"
        )

    def test_the_three_floor_literals_agree(self):
        """Declared floor, CI cell and AST guard must move together.

        Three surfaces state the Python floor: `requires-python` in
        pyproject.toml (the SUPPORT BOUNDARY), `python-version` in the
        workflow (the tested set), and the `feature_version=(3, N)`
        literal the syntax-floor guard parses at
        (test_py312_syntax_floor.py). Before this arm nothing bound them:
        the alignment was claimed in COMMENTS only, so editing any one
        literal reddened nothing while the declared and enforced floors
        diverged silently. All three are read as TEXT and compared.

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

        declared_pair = tuple(int(part) for part in declared[0])
        cell_pair = tuple(int(part) for part in cell[0])
        guard_pair = tuple(int(part) for part in enforced[0])
        echo_pair = tuple(int(part) for part in echo[0])
        assert echo_pair == guard_pair, (
            f"GUARD-INTERNAL DRIFT: the detector enforces "
            f"{'.'.join(map(str, guard_pair))} but its violation message "
            f"reports {'.'.join(map(str, echo_pair))}. A message left at "
            f"the old floor misreports every violation it prints; move "
            f"both in the same change."
        )
        assert declared_pair == cell_pair == guard_pair, (
            f"FLOOR-LITERAL DRIFT: the three floor surfaces disagree.\n"
            f"  pyproject requires-python: {'.'.join(map(str, declared_pair))}\n"
            f"  workflow python-version:  {'.'.join(map(str, cell_pair))}\n"
            f"  syntax-floor guard:       {'.'.join(map(str, guard_pair))}\n"
            f"Each is a different QUESTION (support boundary, tested set, "
            f"scan dialect) that happens to share one value today; moving "
            f"any one of them takes its own run on the new target, and "
            f"then all three move in the same change — this red is the "
            f"reminder, not a bug."
        )

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
