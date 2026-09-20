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
import tempfile
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

# Written INSIDE a guard window by the disposal arms at the foot of this file.
# Bytes, not text: they go to file descriptor 2 directly, which is the only
# writer the guard's own docstring says a `sys.stderr` rebind would miss.
#
# EVERY PART OF THIS PAYLOAD IS LOAD-BEARING -- do not simplify it to a plain
# string. The success arm asserts the replayed bytes are IDENTICAL to these, so
# each element is chosen to make a specific corruption visible:
#   CRLF          a line-ending rewrite collapses it to LF
#   UTF-8 bytes   a decode/re-encode round trip through the wrong codec alters
#                 them, and a lone 0xc2 would be mangled by latin-1
#   NUL + control anything treating the buffer as a C string truncates here
#   no final \n   a "helpfully terminate the line" normalisation appends one
# The whole is also distinctive enough that a coincidental match is not
# credible, which is what makes identity evidence of PROVENANCE.
_WINDOW_PAYLOAD = (
    b"REPLAY-PROVENANCE\r\n"
    b"\xc2\xa7 \xe2\x94\x80 caf\xc3\xa9\n"
    b"\x00\x01\x02 trailing-no-newline"
)


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
        stderr stays empty for a command that emits nothing.

        ⚠️ THAT IS A CLAIM ABOUT THIS COMMAND, NOT ABOUT THE SUCCESS PATH, and
        the wider claim would be false. `_own_stderr_for_envelope` REPLAYS to
        stderr anything written inside the handler window when the command
        succeeds -- measured, and pinned by TestTheGuardDisposesOfCapturedBytes
        below. A successful run whose handler emits (a divergence warning, a
        model-download progress bar) therefore exits 0 with stderr NON-EMPTY and
        is behaving correctly. `list` emits nothing, so there is nothing to
        replay, which is the whole reason this arm is green.

        DO NOT GENERALISE IT BACK. An earlier wording here said stderr "must
        stay EMPTY" on the success path, which contradicts the replay the guard
        performs by design, and would send the next reader of a failure here
        hunting a defect that is not one.
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
            "a `list` wrote to stderr. `list` emits nothing inside the handler "
            "window, so the guard has nothing to replay and this should be "
            "empty. Check for a NEW emitter on the import path or in the list "
            "handler -- do NOT read this as the replay misbehaving, and do not "
            f"relax the assertion to accommodate one: {result.stderr!r}"
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


class TestTheGuardDisposesOfCapturedBytes:
    """What `_own_stderr_for_envelope` does with what it captures.

    WHY THIS ARM EXISTS. Every other arm in this file asserts stderr is pure
    JSON or empty, and every one of them would stay green if the guard's REPLAY
    were deleted outright -- they drive commands that write nothing inside the
    handler window, so there is nothing to replay and its absence is invisible.
    That left the disposal rule itself unpinned while three arms depended on
    understanding it, and left the success-path arm above one emitting command
    away from a failure nobody could diagnose from its message.

    WHY IN-PROCESS, AGAINST THE IDIOM OF THIS FILE. The rest of the module runs
    the CLI as a subprocess, which is right for a contract about streams a
    caller parses. This is a contract about a CONTEXT MANAGER, and reaching it
    through a subprocess would need a command that emits inside the window --
    which today means a save, whose sync path writes the operator's real
    CLAUDE.md. Driving the guard directly tests the same rule and touches no
    store and no file.

    BOTH DIRECTIONS ARE ASSERTED BECAUSE EACH PROTECTS A DIFFERENT THING. The
    discard on failure is what keeps the error envelope alone on the stream.
    The replay on success is what stops a 30-second first-run model download
    looking like a hang. Delete either and one of those regresses silently.
    """

    @staticmethod
    def _bytes_reaching_stderr(fail: bool) -> bytes:
        """Run one guard window writing the payload, return what reached real stderr.

        NON-VACUITY CONTROL, and the arms below are unsound without it. The
        guard FAILS OPEN: when `os.dup(2)` raises it yields `None` and does not
        guard at all. In that state the payload still reaches this sink --
        directly, because nothing captured it -- and the success arm would go
        GREEN while meaning the opposite of what it asserts, namely that the
        guard is inert. Asserting the yielded capture is not None is what makes
        a pass mean the replay ran rather than the mechanism being absent.
        """
        from scripts.cli import _own_stderr_for_envelope

        sink = tempfile.TemporaryFile()
        saved = os.dup(2)
        try:
            os.dup2(sink.fileno(), 2)
            try:
                with _own_stderr_for_envelope() as capture:
                    assert capture is not None, (
                        "the guard failed open (os.dup(2) raised), so this "
                        "window was never guarded and the disposal assertions "
                        "below would be measuring an unguarded write"
                    )
                    os.write(2, _WINDOW_PAYLOAD)
                    if fail:
                        raise SystemExit(1)
            except SystemExit:
                pass
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        sink.seek(0)
        return sink.read()

    def test_a_successful_window_replays_what_it_captured(self):
        """Two assertions, deliberately, because IDENTITY FAILS TWO WAYS.

        Nothing arrived means the replay did not run -- the serious case, and
        the one that makes every diagnostic on a successful command vanish.
        The wrong thing arrived means it ran and wrote something that is not
        the capture -- narrower, and a different repair. One assertion for
        each, so the message names which world you are in rather than leaving
        the next reader to work it out from a bytes diff.

        A separate presence ARM would be redundant: identity ENTAILS presence,
        so such an arm could only fail where this one already failed.

        STATED BOUND: the payload is a single `os.read` chunk, so this does not
        cover a defect in the replay loop's CONTINUATION across chunks. A
        capture larger than 65536 bytes would, at the cost of an unreadable
        failure message.
        """
        landed = self._bytes_reaching_stderr(fail=False)
        assert landed != b"", (
            "NOTHING reached stderr on a successful exit, so the replay did "
            "not run. Every diagnostic emitted during a successful command is "
            "now silently discarded -- a first-run model download reads as a "
            "hang with no output to explain the wait."
        )
        assert landed == _WINDOW_PAYLOAD, (
            "bytes reached stderr but they are NOT the captured bytes, so the "
            "replay is no longer copying the capture verbatim -- check for a "
            "text layer, a line-ending rewrite, or a truncated read.\n"
            f"  expected: {_WINDOW_PAYLOAD!r}\n"
            f"  landed:   {landed!r}"
        )

    def test_a_failed_window_discards_what_it_captured(self):
        """Asserts stderr is EMPTY, not merely that the payload is absent.

        Absence of the payload is satisfied by a replay that ran and wrote
        something else, which is still a replay on the failure path and still
        puts bytes in front of the error envelope. Emptiness is the property
        the envelope's purity actually depends on.
        """
        landed = self._bytes_reaching_stderr(fail=True)
        assert landed == b"", (
            "bytes reached stderr on a FAILED exit, so they sit in front of "
            "the error envelope and every caller parsing stderr breaks. The "
            f"capture must be discarded on this path. Landed: {landed!r}"
        )
